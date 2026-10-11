# pyright: reportUnknownMemberType=false
from datetime import UTC, datetime

import polars as pl
from pydantic import BaseModel

from pitadvisor.features.assemble import EventContext
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.model import backtest
from pitadvisor.outputs.published import PublishedScore, score, stage_of, standing
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION, ForecastDriver


class Finish(BaseModel, frozen=True):
    driver_code: str
    position: int | None
    status: str


class Called(BaseModel, frozen=True):
    stage: str
    run_id: str
    generated_at: datetime
    drivers: list[ForecastDriver]


class DebriefView(BaseModel, frozen=True):
    view: str = "debrief_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    event: EventContext
    # the race forecast standing at lights out, none if nothing was published before it
    called: Called | None
    # empty until the race has a result
    finish: list[Finish]
    record: list[PublishedScore]


def debrief_view(
    store: ObjectStore,
    pane: backtest.Panel,
    sprints: pl.DataFrame,
    context: EventContext,
    run_id: str,
    generated_at: datetime | None = None,
) -> DebriefView:
    """One race's call next to what happened, plus every published race forecast that has a
    result, scored the way the backtest scores. The page lays one over the other."""
    called: Called | None = None
    for kind, view in standing(store):
        if kind != "race" or (view.event.season, view.event.round) != (
            context.season,
            context.round,
        ):
            continue
        if called is None or view.generated_at > called.generated_at:
            called = Called(
                stage=stage_of(view, kind),
                run_id=view.run_id,
                generated_at=view.generated_at,
                drivers=list(view.drivers),
            )
    rows = pane.results.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).drop_nulls("driver_code")
    finish = [
        Finish(
            driver_code=str(row["driver_code"]),
            position=int(row["position"]) if row["position"] is not None else None,
            status=str(row["status"]),
        )
        for row in rows.sort("position", nulls_last=True).iter_rows(named=True)
    ]
    record = [item for item in score(store, pane, sprints) if item.kind == "race"]
    return DebriefView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        event=context,
        called=called,
        finish=finish,
        record=record,
    )
