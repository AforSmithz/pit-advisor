import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, cast

STOPS: Final = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filtered",
}
RETRYABLE: Final = {408, 429, 500, 502, 503, 504}
MAX_WAIT: Final = 60.0


class ChatError(RuntimeError):
    pass


@dataclass
class ChatCompletions:
    """Speaks Bedrock's converse() to an OpenAI-compatible endpoint, so the agent loop and its
    tests stay written against one shape whichever provider answers."""

    base_url: str
    api_key: str
    reasoning_effort: str = ""
    timeout: float = 120.0
    attempts: int = 8
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def converse(self, **request: Any) -> dict[str, Any]:
        return from_chat(self._post(to_chat(request, self.reasoning_effort)))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode()
        url = self.base_url.rstrip("/") + "/chat/completions"
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return cast(dict[str, Any], json.load(response))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                if exc.code not in RETRYABLE or attempt == self.attempts:
                    raise ChatError(f"{exc.code} from {url}: {detail}") from exc
                self.sleep(_wait(exc.headers, attempt))
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == self.attempts:
                    raise ChatError(f"{url} unreachable: {exc}") from exc
                self.sleep(_wait(None, attempt))
        raise ChatError(f"{url} gave up after {self.attempts} attempts")


def _wait(headers: Any, attempt: int) -> float:
    if headers is not None:
        # azure sends both, and the millisecond one is the precise one
        if (ms := headers.get("retry-after-ms")) is not None:
            return min(float(ms) / 1000, MAX_WAIT)
        if (seconds := headers.get("retry-after")) is not None and seconds.isdigit():
            return min(float(seconds), MAX_WAIT)
    return min(2.0**attempt, MAX_WAIT)


def to_chat(request: dict[str, Any], reasoning_effort: str = "") -> dict[str, Any]:
    system = "\n\n".join(block["text"] for block in request.get("system", []) if "text" in block)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for message in request["messages"]:
        messages.extend(_chat_messages(message))
    body: dict[str, Any] = {"model": request["modelId"], "messages": messages}
    tools = [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["inputSchema"]["json"],
            },
        }
        for item in request.get("toolConfig", {}).get("tools", [])
        if (spec := item.get("toolSpec")) is not None
    ]
    if tools:
        body["tools"] = tools
    if (choice := _tool_choice(request.get("toolConfig", {}).get("toolChoice"))) is not None:
        body["tool_choice"] = choice
    config = request.get("inferenceConfig", {})
    if "maxTokens" in config:
        body["max_completion_tokens"] = config["maxTokens"]
    if "temperature" in config:
        body["temperature"] = config["temperature"]
    # gpt-6-luna refuses function tools on chat completions unless this is "none"
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    return body


def _tool_choice(choice: dict[str, Any] | None) -> str | dict[str, Any] | None:
    if not choice:
        return None
    if "tool" in choice:
        return {"type": "function", "function": {"name": choice["tool"]["name"]}}
    if "any" in choice:
        return "required"
    return "auto"


def _chat_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = cast(list[dict[str, Any]], message.get("content", []))
    text = "\n".join(block["text"] for block in blocks if "text" in block)
    if message["role"] == "assistant":
        calls = [
            {
                "id": block["toolUse"]["toolUseId"],
                "type": "function",
                "function": {
                    "name": block["toolUse"]["name"],
                    "arguments": json.dumps(block["toolUse"].get("input", {})),
                },
            }
            for block in blocks
            if "toolUse" in block
        ]
        out: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            out["tool_calls"] = calls
        return [out]
    results = [
        {
            "role": "tool",
            "tool_call_id": block["toolResult"]["toolUseId"],
            "content": _result_text(block["toolResult"]),
        }
        for block in blocks
        if "toolResult" in block
    ]
    return results + ([{"role": "user", "content": text}] if text else [])


def _result_text(result: dict[str, Any]) -> str:
    parts = cast(list[dict[str, Any]], result.get("content", []))
    return "\n".join(
        json.dumps(part["json"]) if "json" in part else str(part.get("text", "")) for part in parts
    )


def from_chat(response: dict[str, Any]) -> dict[str, Any]:
    choices = cast(list[dict[str, Any]], response.get("choices") or [])
    if not choices:
        raise ChatError(f"no choices in the response: {str(response)[:300]}")
    choice = choices[0]
    message = cast(dict[str, Any], choice.get("message") or {})
    content: list[dict[str, Any]] = []
    if text := str(message.get("content") or "").strip():
        content.append({"text": text})
    for call in cast(list[dict[str, Any]], message.get("tool_calls") or []):
        content.append(
            {
                "toolUse": {
                    "toolUseId": call["id"],
                    "name": call["function"]["name"],
                    "input": _arguments(call["function"].get("arguments")),
                }
            }
        )
    finish = str(choice.get("finish_reason") or "")
    stop = STOPS.get(finish, finish)
    if any("toolUse" in block for block in content):
        stop = "tool_use"
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": _usage(response.get("usage") or {}),
    }


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        parsed = None
    # kept rather than dropped, so the tool's validation fails loudly instead of running on defaults
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {"_unparsed": raw}


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt = cast(dict[str, Any], usage.get("prompt_tokens_details") or {})
    completion = cast(dict[str, Any], usage.get("completion_tokens_details") or {})
    mapped: dict[str, Any] = {
        "inputTokens": usage.get("prompt_tokens"),
        "outputTokens": usage.get("completion_tokens"),
        "totalTokens": usage.get("total_tokens"),
        "cacheReadInputTokens": prompt.get("cached_tokens"),
        "reasoningTokens": completion.get("reasoning_tokens"),
    }
    return {key: value for key, value in mapped.items() if isinstance(value, int)}
