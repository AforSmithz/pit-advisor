import json
import re
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel

from pitadvisor.quality.contracts import IncidentArticleRow, IncidentRow, IncidentSanctionRow
from pitadvisor.types import Layer

from .parse import Decision
from .sanctions import sanctions

KINDS: Final = ("decision", "offence", "infringement")
STAMPED: Final = re.compile(r"-\d{8}T\d{9}Z\.[A-Za-z0-9]+$")
PARSED: Final = "parsed"
EXTRACTED: Final = "extracted"


class Reading(BaseModel, frozen=True):
    """One document's decisions, and where they came from."""

    raw_key: str
    document_name: str = ""
    kind: str
    read_by: str
    decisions: list[Decision] = []
    unverified: list[str] = []
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def kind_of(raw_key: str) -> str | None:
    # the published name is a timestamp then the kind then the title, but matching on the word
    # rather than on its surrounding dashes keeps this working for a name written any other way
    words = raw_key.rsplit("/", 1)[-1].split("-")
    return next((word for word in words if word in KINDS), None)


def cache_key(raw_key: str) -> str:
    return f"{Layer.CACHE}/incidents/{raw_key.removeprefix(f'{Layer.RAW}/')}.json"


def dump(reading: Reading) -> bytes:
    return reading.model_dump_json(indent=2).encode()


def load(body: bytes) -> Reading:
    return Reading.model_validate_json(body)


def _loose(unverified: list[str], entry: int) -> list[str]:
    head = f"{entry}."
    return [name.removeprefix(head) for name in unverified if name.startswith(head)]


class Rows(BaseModel, frozen=True):
    incidents: list[IncidentRow] = []
    articles: list[IncidentArticleRow] = []
    sanctions: list[IncidentSanctionRow] = []


def document_name(raw_key: str) -> str:
    """The published name with our fetch stamp taken off, which is stable across a refetch."""
    return STAMPED.sub("", raw_key.rsplit("/", 1)[-1])


def rows(reading: Reading, season: int, round_: int, stamp: dict[str, Any]) -> Rows:
    named = reading.document_name or document_name(reading.raw_key)
    incidents: list[IncidentRow] = []
    articles: list[IncidentArticleRow] = []
    imposed: list[IncidentSanctionRow] = []
    for entry, decision in enumerate(reading.decisions):
        incidents.append(
            IncidentRow(
                **stamp,
                season=season,
                round=round_,
                document_name=named,
                document=decision.document,
                entry=entry,
                kind=reading.kind,
                issued=decision.issued,
                car=decision.car,
                driver=decision.driver,
                competitor=decision.competitor,
                session=decision.session,
                fact=decision.fact,
                charge=decision.charge,
                outcome=decision.outcome,
                reason=decision.reason,
                read_by=reading.read_by,
                unverified=_loose(reading.unverified, entry),
                raw_key=reading.raw_key,
            )
        )
        articles += [
            IncidentArticleRow(
                **stamp,
                season=season,
                round=round_,
                document_name=named,
                document=decision.document,
                entry=entry,
                code=article.code,
                regulation=article.regulation,
                book=article.book,
                edition=article.edition,
                raw_key=reading.raw_key,
            )
            for article in decision.articles
        ]
        imposed += [
            IncidentSanctionRow(
                **stamp,
                season=season,
                round=round_,
                document_name=named,
                document=decision.document,
                entry=entry,
                ordinal=ordinal,
                kind=sanction.kind,
                seconds=sanction.seconds,
                positions=sanction.positions,
                points=sanction.points,
                points_total=sanction.points_total,
                amount=sanction.amount,
                currency=sanction.currency,
                text=sanction.text,
                raw_key=reading.raw_key,
            )
            for ordinal, sanction in enumerate(sanctions(decision.outcome))
        ]
    return Rows(incidents=incidents, articles=articles, sanctions=imposed)


def migrate(record: dict[str, Any]) -> Reading:
    """Rebuilds a Reading from a line of the pilot's jsonl, so its spend is not repeated."""
    raw_key = str(record["key"])
    kind = kind_of(raw_key)
    return Reading(
        raw_key=raw_key,
        kind=kind or "decision",
        read_by=EXTRACTED,
        decisions=[Decision.model_validate(found) for found in record["decisions"]],
        unverified=list(record["unverified"]),
        error=record["error"],
        input_tokens=int(record["input_tokens"]),
        output_tokens=int(record["output_tokens"]),
    )


def read_jsonl(text: str) -> list[Reading]:
    latest: dict[str, Reading] = {}
    for line in text.splitlines():
        found = migrate(json.loads(line))
        latest[found.raw_key] = found
    return [found for found in latest.values() if found.error is None]


def stamped(run_id: str) -> dict[str, Any]:
    return {"run_id": run_id, "ingested_at": datetime.now(UTC)}
