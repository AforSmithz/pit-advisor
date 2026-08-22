import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from pitadvisor.ingest.ratelimit import BucketState
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.quality.checks import QualityReport, Status
from pitadvisor.types import Layer, Source

SCHEMA_VERSION = "1.0.0"

TABLE_SOURCE: dict[str, Source] = {
    "races": Source.JOLPICA,
    "results": Source.JOLPICA,
    "qualifying": Source.JOLPICA,
    "laps": Source.JOLPICA,
    "pitstops": Source.JOLPICA,
    "weather": Source.OPEN_METEO,
    "session_laps": Source.FASTF1,
}


class TableHealth(BaseModel, frozen=True):
    table: str
    source: Source
    status: Status
    detail: str


class QuarantineSummary(BaseModel, frozen=True):
    table: str
    reason: str
    rows: int
    explained: bool


class QuotaUsage(BaseModel, frozen=True):
    name: str
    capacity: int
    tokens_left: float
    refill_per_second: float
    measured_at: datetime


class PipelineView(BaseModel, frozen=True):
    view: str = "pipeline_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    layer: Layer
    healthy: bool
    tables: list[TableHealth]
    quarantine: list[QuarantineSummary]
    quota: list[QuotaUsage]


def _worst(statuses: list[Status]) -> Status:
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.WARN in statuses:
        return Status.WARN
    return Status.OK


def pipeline_view(
    report: QualityReport,
    run_id: str,
    quota: list[BucketState] | None = None,
    generated_at: datetime | None = None,
) -> PipelineView:
    per_table: dict[str, list[str]] = {}
    statuses: dict[str, list[Status]] = {}
    for outcome in report.outcomes:
        if outcome.table == "-":
            continue
        per_table.setdefault(outcome.table, []).append(f"{outcome.check}: {outcome.detail}")
        statuses.setdefault(outcome.table, []).append(outcome.status)
    tables = [
        TableHealth(
            table=table,
            source=TABLE_SOURCE.get(table, Source.CURATED),
            status=_worst(statuses[table]),
            detail="; ".join(details),
        )
        for table, details in sorted(per_table.items())
    ]
    return PipelineView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        layer=report.layer,
        healthy=report.ok,
        tables=tables,
        quarantine=[QuarantineSummary(**item.model_dump()) for item in report.quarantine],
        quota=[
            QuotaUsage(
                name=state.name,
                capacity=state.capacity,
                tokens_left=round(state.tokens, 2),
                refill_per_second=state.refill_per_second,
                measured_at=state.updated_at,
            )
            for state in (quota or [])
        ],
    )


def view_key(name: str) -> str:
    return f"{Layer.VIEWS}/{name}.json"


def emit(store: ObjectStore, view: BaseModel) -> str:
    payload: dict[str, Any] = view.model_dump(mode="json")
    name = str(payload.get("view") or "view")
    return store.put(view_key(name), json.dumps(payload, indent=2).encode())
