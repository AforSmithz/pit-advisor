from datetime import UTC, date, datetime
from typing import Final

from pydantic import BaseModel

from pitadvisor.features.assemble import EventContext
from pitadvisor.features.weather import ScenarioWeights
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.outputs.view_contracts import (
    SCHEMA_VERSION,
    Estimate,
    ForecastView,
    TrackView,
    WeekendView,
    view_key,
)

FAVOURITES: Final = 3
FORM_LEADERS: Final = 3
NEIGHBOURS: Final = 3


class StaleViewsError(RuntimeError):
    pass


class Source(BaseModel, frozen=True):
    view: str
    run_id: str
    generated_at: datetime
    as_of: date


class Favourite(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    grid: int | None
    win: float
    podium: float
    position_low: int
    position_high: int


class FormLeader(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    form: Estimate


class TrackNote(BaseModel, frozen=True):
    circuit_id: str
    length_km: float
    corners: int
    neighbours: list[str]
    # constructors whose two track-fit estimators do not overlap, a finding worth a line
    disagreements: list[str]


class BriefView(BaseModel, frozen=True):
    """The deterministic weekend brief. Every value is copied from a view that passed the
    gate; picking the top few is the only thing done here. The agent-written brief replaces
    it once bedrock text generation opens (DECISIONS.md, 2026-10-07)."""

    view: str = "brief_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    event: EventContext
    weather: ScenarioWeights | None
    favourites: list[Favourite]
    simulated_paths: int
    grid_sampled: bool
    beats_baselines: bool | None
    separated_from: list[str]
    form_leaders: list[FormLeader]
    track: TrackNote
    sources: list[Source]


def _same_event(*views: WeekendView | ForecastView | TrackView) -> EventContext:
    events = {(view.event.season, view.event.round) for view in views}
    if len(events) != 1:
        found = ", ".join(f"{view.view} {view.event.season}:{view.event.round}" for view in views)
        raise StaleViewsError(f"the views describe different races: {found}")
    return views[0].event


def brief_view(
    weekend: WeekendView,
    forecast: ForecastView,
    track: TrackView,
    run_id: str,
    generated_at: datetime | None = None,
) -> BriefView:
    event = _same_event(weekend, forecast, track)
    favourites = sorted(forecast.drivers, key=lambda row: (-row.win, row.driver_code))[:FAVOURITES]
    # the lake holds five seasons of drivers, the brief is about the ones still racing
    current = max((row.last_season for row in weekend.drivers), default=event.season)
    rated = [row for row in weekend.drivers if row.form is not None and row.last_season == current]
    # negative is faster than the reference, so the leaders are the lowest values
    leaders = sorted(rated, key=lambda row: (row.form.value if row.form else 0.0, row.driver_code))
    return BriefView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        event=event,
        weather=weekend.weather,
        favourites=[
            Favourite(
                driver_code=row.driver_code,
                constructor_id=row.constructor_id,
                grid=row.grid,
                win=row.win,
                podium=row.podium,
                position_low=row.position_low,
                position_high=row.position_high,
            )
            for row in favourites
        ],
        simulated_paths=forecast.paths,
        grid_sampled=forecast.grid_sampled,
        beats_baselines=forecast.evidence.beats_baselines if forecast.evidence else None,
        separated_from=forecast.evidence.separated_from if forecast.evidence else [],
        form_leaders=[
            FormLeader(
                driver_code=row.driver_code, constructor_id=row.constructor_id, form=row.form
            )
            for row in leaders[:FORM_LEADERS]
            if row.form is not None
        ],
        track=TrackNote(
            circuit_id=track.profile.circuit_id,
            length_km=track.profile.length_km,
            corners=track.profile.corners,
            # a circuit is its own nearest neighbour, which says nothing
            neighbours=[
                item.circuit_id
                for item in track.neighbours
                if item.circuit_id != track.profile.circuit_id
            ][:NEIGHBOURS],
            disagreements=sorted(team.constructor_id for team in track.teams if team.disagree),
        ),
        sources=[
            Source(
                view=view.view, run_id=view.run_id, generated_at=view.generated_at, as_of=view.as_of
            )
            for view in (weekend, forecast, track)
        ],
    )


def from_store(store: ObjectStore, run_id: str) -> BriefView:
    missing = [
        name
        for name in ("weekend_view", "forecast_view", "track_view")
        if not store.exists(view_key(name))
    ]
    if missing:
        raise StaleViewsError(f"emit {', '.join(missing)} before the brief")
    return brief_view(
        WeekendView.model_validate_json(store.get(view_key("weekend_view"))),
        ForecastView.model_validate_json(store.get(view_key("forecast_view"))),
        TrackView.model_validate_json(store.get(view_key("track_view"))),
        run_id,
    )
