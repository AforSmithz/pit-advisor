import re
from dataclasses import dataclass, field
from typing import Any, Final, cast

from botocore.config import Config
from pydantic import BaseModel

from pitadvisor.agent import prompts, tools
from pitadvisor.agent.tools import Toolbox, ToolResult
from pitadvisor.config import Settings, boto_session

HAIKU: Final = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_ITERATIONS: Final = 6
MAX_TOKENS: Final = 1024
FIGURE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
# the model writes a minus as U+2212 about half the time, which is not the character the
# regex above is looking for, and a figure whose sign went missing looks invented
DASHES = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-"})
# a year or a round number in the question is the asker's, not a figure the model invented
SAFE = re.compile(r"\b(19|20)\d{2}s?\b")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# text that looks numeric and is not a measurement. a timestamp split into pieces invents five
# figures, "Formula 1" invents one, and a numbered list invents as many as it has items
NOT_A_FIGURE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    rf"|\b(?:{MONTHS})\s+\d{{1,2}}\b"
    r"|\bFormula\s*(?:1|One)\b"
    r"|\bF1\b"
    r"|^\s*\d+[.)]\s",
    re.MULTILINE,
)


class ToolCall(BaseModel, frozen=True):
    name: str
    arguments: dict[str, Any]
    ok: bool
    detail: str = ""


class Answer(BaseModel, frozen=True):
    text: str
    question: str
    calls: list[ToolCall]
    stop_reason: str
    iterations: int
    usage: dict[str, int]
    ungrounded: list[str] = []
    refused: bool = False

    @property
    def grounded(self) -> bool:
        return not self.ungrounded

    @property
    def tools_used(self) -> list[str]:
        return [call.name for call in self.calls]


@dataclass
class Agent:
    client: Any
    toolbox: Toolbox
    model_id: str = HAIKU
    system: str = prompts.SYSTEM
    max_iterations: int = MAX_ITERATIONS
    max_tokens: int = MAX_TOKENS
    guardrail: tuple[str, str] | None = None
    strict: bool = True
    _specs: list[dict[str, Any]] = field(default_factory=tools.specs)

    def ask(self, question: str) -> Answer:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": question}]},
        ]
        calls: list[ToolCall] = []
        results: list[ToolResult] = []
        usage: dict[str, int] = {}
        stop = "max_iterations"
        text = ""
        for iteration in range(1, self.max_iterations + 1):
            response = self._converse(messages)
            _accumulate(usage, response.get("usage", {}))
            output = response["output"]["message"]
            messages.append(output)
            stop = str(response.get("stopReason", ""))
            blocks = cast(list[dict[str, Any]], output.get("content", []))
            text = "\n".join(block["text"].strip() for block in blocks if "text" in block).strip()
            if stop != "tool_use":
                return self._finish(question, text, calls, results, stop, iteration, usage)
            messages.append(self._answer_tools(blocks, calls, results))
        return self._finish(question, text, calls, results, stop, self.max_iterations, usage)

    def _converse(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": messages,
            # cache points on the two static blocks. haiku 4.5 wants 4096 tokens before a
            # checkpoint does anything and this prompt is well under it, so today they are
            # inert; they cost nothing and start working if the prompt grows or the model
            # changes to one with a lower floor
            "system": [{"text": self.system}, {"cachePoint": {"type": "default"}}],
            "toolConfig": {"tools": [*self._specs, {"cachePoint": {"type": "default"}}]},
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": 0.0},
        }
        if self.guardrail is not None:
            identifier, version = self.guardrail
            request["guardrailConfig"] = {
                "guardrailIdentifier": identifier,
                "guardrailVersion": version,
            }
        return cast(dict[str, Any], self.client.converse(**request))

    def _answer_tools(
        self,
        blocks: list[dict[str, Any]],
        calls: list[ToolCall],
        results: list[ToolResult],
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for block in blocks:
            use = block.get("toolUse")
            if use is None:
                continue
            arguments = cast(dict[str, Any], use.get("input", {}))
            result = tools.invoke(self.toolbox, str(use["name"]), arguments)
            results.append(result)
            calls.append(
                ToolCall(
                    name=str(use["name"]),
                    arguments=arguments,
                    ok=result.ok,
                    detail=result.detail,
                )
            )
            content.append(
                {
                    "toolResult": {
                        "toolUseId": use["toolUseId"],
                        "content": [{"json": result.model_dump(mode="json")}],
                        "status": "success" if result.ok else "error",
                    }
                }
            )
        return {"role": "user", "content": content}

    def _finish(
        self,
        question: str,
        text: str,
        calls: list[ToolCall],
        results: list[ToolResult],
        stop: str,
        iterations: int,
        usage: dict[str, int],
    ) -> Answer:
        loose = ungrounded(text, question, calls, results)
        refused = bool(loose) and self.strict
        return Answer(
            text=_withheld(loose) if refused else text,
            question=question,
            calls=calls,
            stop_reason=stop,
            iterations=iterations,
            usage=usage,
            ungrounded=loose,
            refused=refused,
        )


def _withheld(loose: list[str]) -> str:
    figures = ", ".join(loose)
    return (
        "Withheld. The answer carried figures no tool returned "
        f"({figures}), and this system does not publish a number it cannot trace."
    )


def _accumulate(totals: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            totals[key] = totals.get(key, 0) + value


def grounding_set(question: str, calls: list[ToolCall], results: list[ToolResult]) -> set[str]:
    allowed: set[str] = set()
    for result in results:
        if result.ok:
            allowed |= tools.numbers_in(result)
    for call in calls:
        allowed |= {_normalised(match) for match in FIGURE.findall(str(call.arguments))}
    allowed |= {_normalised(match) for match in FIGURE.findall(question)}
    return allowed


def ungrounded(
    text: str, question: str, calls: list[ToolCall], results: list[ToolResult]
) -> list[str]:
    allowed = grounding_set(question, calls, results)
    scanned = tools.RANGE.sub(" ", NOT_A_FIGURE.sub(" ", text.translate(DASHES)))
    years = {match.group(0).rstrip("s") for match in SAFE.finditer(scanned)}
    loose: list[str] = []
    for raw in FIGURE.findall(scanned):
        cleaned = _normalised(raw)
        if cleaned in allowed or cleaned in years:
            continue
        if cleaned not in loose:
            loose.append(cleaned)
    return loose


def _normalised(raw: str) -> str:
    cleaned = raw.replace(",", "").lstrip("+")
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    return cleaned


def agent_for(settings: Settings, box: Toolbox, strict: bool = True) -> Agent:
    session = cast(Any, boto_session(settings))
    return Agent(
        client=session.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            # adaptive, because a run of the golden set is a burst against a per-minute quota
            # and the default standard mode gives up after four tries
            config=Config(retries={"max_attempts": 8, "mode": "adaptive"}),
        ),
        toolbox=box,
        model_id=settings.bedrock_model,
        guardrail=(
            (settings.guardrail_id, settings.guardrail_version) if settings.guardrail_id else None
        ),
        strict=strict,
    )
