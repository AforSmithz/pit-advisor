from datetime import UTC, datetime

from pydantic import BaseModel

from pitadvisor.features.assemble import EventContext
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.outputs.published import HISTORY, Kind, stage_of
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION, ForecastView


class Standing(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    grid: int | None
    win: float
    podium: float
    points: float


class Snapshot(BaseModel, frozen=True):
    kind: str
    stage: str
    run_id: str
    generated_at: datetime
    # the same numbers as the snapshot before it: a rerun that changed nothing
    repeat: bool
    drivers: list[Standing]


class HistoryView(BaseModel, frozen=True):
    view: str = "history_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    event: EventContext
    snapshots: list[Snapshot]


def _snapshot(view: ForecastView, kind: Kind, previous: list[Standing] | None) -> Snapshot:
    drivers = [
        Standing(
            driver_code=item.driver_code,
            constructor_id=item.constructor_id,
            grid=item.grid,
            win=item.win,
            podium=item.podium,
            points=item.points,
        )
        for item in view.drivers
    ]
    return Snapshot(
        kind=kind,
        stage=stage_of(view, kind),
        run_id=view.run_id,
        generated_at=view.generated_at,
        repeat=previous is not None and previous == drivers,
        drivers=drivers,
    )


def history_view(
    store: ObjectStore, context: EventContext, run_id: str, generated_at: datetime | None = None
) -> HistoryView:
    """Every forecast published for this event before its session, oldest first, so the page can
    show how the call moved through the weekend without computing anything itself."""
    prefix = f"{HISTORY}season={context.season}/round={context.round:02d}/"
    found: list[tuple[Kind, ForecastView]] = []
    for item in store.list(prefix):
        name = item.key.rsplit("/", 1)[-1]
        kind: Kind = "sprint" if name.startswith("sprint-") else "race"
        found.append((kind, ForecastView.model_validate_json(store.get(item.key))))
    found.sort(key=lambda pair: pair[1].generated_at)
    snapshots: list[Snapshot] = []
    last: dict[str, list[Standing]] = {}
    for kind, view in found:
        snapshot = _snapshot(view, kind, last.get(kind))
        last[kind] = snapshot.drivers
        snapshots.append(snapshot)
    return HistoryView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        event=context,
        snapshots=snapshots,
    )
