import json
from typing import Any, Final, cast

from pydantic import BaseModel

from .parse import Decision, Span, articles, collapse, locate, normalize, parse

TOOL: Final = "record_decision"

QUOTE = "Copy this exactly as the document writes it, or null if the document does not say."

SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "car": {"type": ["integer", "null"], "description": "Car number."},
                    "driver": {"type": ["string", "null"], "description": QUOTE},
                    "competitor": {"type": ["string", "null"], "description": QUOTE},
                    "session": {"type": ["string", "null"], "description": QUOTE},
                    "fact": {"type": ["string", "null"], "description": QUOTE},
                    "charge": {"type": ["string", "null"], "description": QUOTE},
                    "outcome": {"type": ["string", "null"], "description": QUOTE},
                    "reason": {"type": ["string", "null"], "description": QUOTE},
                },
                "required": [
                    "car",
                    "driver",
                    "competitor",
                    "session",
                    "fact",
                    "charge",
                    "outcome",
                    "reason",
                ],
            },
        }
    },
    "required": ["entries"],
}

SYSTEM: Final = """You read Formula 1 stewards' documents and record what they say.

Every text value you return must be a substring of the document, copied character for character.
Keep the document's own spelling, spacing and punctuation, including mistakes: if it says
"Geor ge Russell" or "Estaban Ocon", that is what you return. Do not tidy, translate, summarise,
shorten or join text that the document keeps apart. A value you cannot copy is null.

One entry per car the document rules on. A document that penalises four cars is four entries and a
document that names no car is one entry with a null car. Fields the document states once for all of
them, such as the session or the charge, are repeated in every entry.

fact is what the driver is said to have done, charge is the rule said to have been broken, outcome
is what the stewards decided, and reason is their reasoning. A document that grants a request or
schedules a hearing has no charge and no outcome; leave them null rather than inventing them.

Never write a number, a name or a rule article that is not in the document."""


class Extraction(BaseModel, frozen=True):
    decisions: list[Decision] = []
    unverified: list[str] = []
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def clean(self) -> bool:
        return self.error is None and not self.unverified and bool(self.decisions)


def tool_config() -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": TOOL,
                    "description": "Record every decision the document makes, one entry per car.",
                    "inputSchema": {"json": SCHEMA},
                }
            }
        ],
        "toolChoice": {"tool": {"name": TOOL}},
    }


def _entries(response: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    for block in blocks:
        use = block.get("toolUse")
        if use and use.get("name") == TOOL:
            return cast(list[dict[str, Any]], use.get("input", {}).get("entries", []))
    return []


FIELDS: Final = ("driver", "competitor", "session", "fact", "charge", "outcome", "reason")


def _decision(entry: dict[str, Any], header: Decision, source: str) -> tuple[Decision, list[str]]:
    # what the model returns is a quote it typed, so it is replaced by the substring of the
    # document it points at and dropped when it points at nothing
    fields: dict[str, Any] = {}
    loose: list[str] = []
    for name in FIELDS:
        value = entry.get(name)
        found = locate(value, source) if value else None
        if value and found is None:
            loose.append(name)
        fields[name] = found
    spans = [Span(field=name, text=value) for name, value in fields.items() if value]
    return (
        header.model_copy(
            update={
                "car": entry.get("car"),
                **fields,
                "articles": articles(fields["charge"]),
                "spans": spans,
            }
        ),
        loose,
    )


def extract(
    client: Any,
    model_id: str,
    raw: str,
    max_tokens: int = 4096,
) -> Extraction:
    header = parse(raw)
    text = normalize(raw)
    response: dict[str, Any] = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": text}]}],
        system=[{"text": SYSTEM}],
        toolConfig=tool_config(),
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
    )
    usage = response.get("usage", {})
    folded = collapse(raw)
    decisions: list[Decision] = []
    unverified: list[str] = []
    for index, entry in enumerate(_entries(response)):
        found, loose = _decision(entry, header, folded)
        decisions.append(found)
        unverified += [f"{index}.{name}" for name in loose]
    return Extraction(
        decisions=decisions,
        unverified=unverified,
        input_tokens=int(usage.get("inputTokens", 0)),
        output_tokens=int(usage.get("outputTokens", 0)),
        error=None if decisions else "the model recorded no entries",
    )


def as_json(extraction: Extraction) -> str:
    return json.dumps([found.model_dump(mode="json") for found in extraction.decisions], indent=2)
