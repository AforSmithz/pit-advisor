from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel

from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION
from pitadvisor.quality.checks import QualityReport
from pitadvisor.quality.lineage import RAW_ORIGIN, raw_count

# which bronze tables each view is computed from. the forecast reads bronze through the
# panel, not the gold marts, so its trace stops at bronze and raw rather than inventing a mart
INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "forecast_view": (
        "races",
        "results",
        "qualifying",
        "sprint",
        "session_laps",
        "weather",
        "incidents",
        "incident_sanctions",
        "car_updates",
        "market_odds",
    ),
    "history_view": ("races", "results", "qualifying", "session_laps", "weather"),
    "weekend_view": ("races", "results", "qualifying", "session_laps", "weather"),
    "driver_view": ("races", "results", "session_laps"),
    "track_view": ("races", "results", "session_laps"),
    "calibration_view": ("races", "results", "qualifying", "session_laps", "market_odds"),
    "debrief_view": ("races", "results"),
    "stewards_view": ("incidents", "incident_sanctions", "incident_articles", "results"),
    "upgrades_view": ("car_updates", "races"),
}


class TableTrace(BaseModel, frozen=True):
    table: str
    source: str | None
    status: str
    rows: str
    freshness: str
    raw_objects: int


class LineageView(BaseModel, frozen=True):
    view: str = "lineage_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    views: dict[str, list[str]]
    tables: list[TableTrace]


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "absent"
    for status in ("fail", "warn"):
        if status in statuses:
            return status
    return "ok"


def lineage_view(
    store: ObjectStore, report: QualityReport, run_id: str, generated_at: datetime | None = None
) -> LineageView:
    """For every view, the bronze tables it stands on, each with the quality gate's verdict and
    how many raw objects it was rebuilt from, so a number on the page can be walked back."""
    wanted = sorted({table for tables in INPUTS.values() for table in tables})
    checks: dict[tuple[str, str], tuple[str, str]] = {
        (outcome.table, outcome.check): (str(outcome.status), outcome.detail)
        for outcome in report.outcomes
    }
    traces: list[TableTrace] = []
    for table in wanted:
        rows = checks.get((table, "row_count"))
        fresh = checks.get((table, "freshness"))
        statuses = [item[0] for item in (rows, fresh) if item is not None]
        origin = RAW_ORIGIN.get(table)
        traces.append(
            TableTrace(
                table=table,
                source=str(origin[0]) if origin else None,
                status=_worst(statuses),
                rows=rows[1] if rows else "",
                freshness=fresh[1] if fresh else "",
                raw_objects=raw_count(store, table),
            )
        )
    return LineageView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        views={name: list(tables) for name, tables in INPUTS.items()},
        tables=traces,
    )
