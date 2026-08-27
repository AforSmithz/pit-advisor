import json
import math
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
from pydantic import BaseModel

from pitadvisor.features.assemble import Assembled, Coverage, EventContext
from pitadvisor.features.form import pairings as form_pairings
from pitadvisor.features.reliability import Cause as ReliabilityCause
from pitadvisor.features.track_fit import CircuitProfile, Neighbour
from pitadvisor.features.track_fit import load as load_circuits
from pitadvisor.features.weather import ScenarioWeights
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


class Estimate(BaseModel, frozen=True):
    """§6.2: nothing reaches the page as a bare number. The interval travels with it."""

    value: float
    low: float
    high: float
    samples: int = 0


class WeekendDriver(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    form: Estimate | None
    # two drivers with no shared car lineage are not comparable, whatever their effects say
    form_component: int | None
    quali_race: Estimate | None
    wet: Estimate | None


class WeekendTeam(BaseModel, frozen=True):
    constructor_id: str
    track_fit_regression: Estimate | None
    track_fit_similarity: Estimate | None
    estimators_disagree: bool
    dnf_per_lap: Estimate | None
    wet: Estimate | None


class WeekendView(BaseModel, frozen=True):
    view: str = "weekend_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    as_of: date
    event: EventContext
    drivers: list[WeekendDriver]
    teams: list[WeekendTeam]
    weather: ScenarioWeights | None
    neighbours: list[Neighbour]
    coverage: Coverage
    # how much of the dnf split is real. the pooled hazard is the number that always means
    # something, this says whether the per-cause one does
    cause_coverage: float


class PaceSample(BaseModel, frozen=True):
    season: int
    round: int
    race_date: date
    circuit_id: str
    regime: str
    percent_off_benchmark: float


class TeammateSample(BaseModel, frozen=True):
    season: int
    round: int
    race_date: date
    circuit_id: str
    teammate: str
    delta: float


class DriverHistory(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    form: Estimate | None
    form_component: int | None
    quali_race: Estimate | None
    wet: Estimate | None
    pace: list[PaceSample]
    teammate: list[TeammateSample]


class DriverView(BaseModel, frozen=True):
    view: str = "driver_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    as_of: date
    event: EventContext
    half_life_events: float
    drivers: list[DriverHistory]


class TeamAtTrack(BaseModel, frozen=True):
    constructor_id: str
    regression: Estimate | None
    similarity: Estimate | None
    disagree: bool
    history: list[PaceSample]


class TrackView(BaseModel, frozen=True):
    view: str = "track_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    as_of: date
    event: EventContext
    profile: CircuitProfile
    neighbours: list[Neighbour]
    teams: list[TeamAtTrack]
    dropped_reprofiled: int


def _estimate(value: float, low: float, high: float, samples: int = 0) -> Estimate | None:
    if not all(math.isfinite(number) for number in (value, low, high)):
        return None
    return Estimate(value=value, low=low, high=high, samples=samples)


def _seats(pace: pl.DataFrame) -> dict[str, str]:
    """The car a driver was in most recently, which is the one he is about to race."""
    ordered = pace.sort("race_date")
    return {
        str(row["driver_code"]): str(row["constructor_id"])
        for row in ordered.select("driver_code", "constructor_id").iter_rows(named=True)
    }


def weekend_view(assembled: Assembled) -> WeekendView:
    metrics = assembled.metrics
    seats = _seats(assembled.pace)
    form_by = {item.driver_code: item for item in metrics.form.drivers}
    quali_by = {item.driver_code: item for item in metrics.quali.drivers}
    wet_driver = {item.key: item for item in metrics.wet.drivers}
    wet_team = {item.key: item for item in metrics.wet.teams}
    regression = {item.constructor_id: item for item in metrics.track.regression}
    similarity = {item.constructor_id: item for item in metrics.track.similarity}
    pooled = {
        item.key: item for item in metrics.reliability.teams if item.cause is ReliabilityCause.ANY
    }

    drivers = [
        WeekendDriver(
            driver_code=code,
            constructor_id=seats[code],
            form=(
                _estimate(item.effect, item.interval_low, item.interval_high, item.events)
                if (item := form_by.get(code))
                else None
            ),
            form_component=item.component if (item := form_by.get(code)) else None,
            quali_race=(
                _estimate(trend.delta, trend.interval_low, trend.interval_high, trend.events)
                if (trend := quali_by.get(code))
                else None
            ),
            wet=(
                _estimate(
                    rain.shrunk_delta, rain.interval_low, rain.interval_high, rain.wet_sessions
                )
                if (rain := wet_driver.get(code))
                else None
            ),
        )
        for code in sorted(seats)
    ]
    teams = [
        WeekendTeam(
            constructor_id=team,
            track_fit_regression=(
                _estimate(
                    fitted.estimate, fitted.interval_low, fitted.interval_high, fitted.samples
                )
                if (fitted := regression.get(team))
                else None
            ),
            track_fit_similarity=(
                _estimate(close.estimate, close.interval_low, close.interval_high, close.samples)
                if (close := similarity.get(team))
                else None
            ),
            estimators_disagree=team in metrics.track.disagreements,
            dnf_per_lap=(
                _estimate(
                    hazard.per_lap,
                    hazard.interval_low,
                    hazard.interval_high,
                    int(hazard.weighted_failures),
                )
                if (hazard := pooled.get(team))
                else None
            ),
            wet=(
                _estimate(
                    rain.shrunk_delta, rain.interval_low, rain.interval_high, rain.wet_sessions
                )
                if (rain := wet_team.get(team))
                else None
            ),
        )
        for team in sorted(set(seats.values()))
    ]
    return WeekendView(
        generated_at=metrics.generated_at,
        run_id=metrics.run_id,
        as_of=metrics.as_of,
        event=metrics.context,
        drivers=drivers,
        teams=teams,
        weather=metrics.weather,
        neighbours=metrics.track.neighbours,
        coverage=metrics.coverage,
        cause_coverage=metrics.reliability.cause_coverage,
    )


def _samples(rows: pl.DataFrame) -> list[PaceSample]:
    return [
        PaceSample(
            season=int(row["season"]),
            round=int(row["round"]),
            race_date=row["race_date"],
            circuit_id=str(row["circuit_id"]),
            regime=str(row["regime"]),
            percent_off_benchmark=float(row["value"]),
        )
        for row in rows.sort("race_date").iter_rows(named=True)
    ]


def driver_view(assembled: Assembled) -> DriverView:
    metrics = assembled.metrics
    history = assembled.pace.filter(pl.col("race_date") < metrics.as_of)
    seats = _seats(history)
    form_by = {item.driver_code: item for item in metrics.form.drivers}
    quali_by = {item.driver_code: item for item in metrics.quali.drivers}
    wet_by = {item.key: item for item in metrics.wet.drivers}
    pairs = form_pairings(history.filter(~pl.col("is_wet")))

    drivers = [
        DriverHistory(
            driver_code=code,
            constructor_id=seats[code],
            form=(
                _estimate(item.effect, item.interval_low, item.interval_high, item.events)
                if (item := form_by.get(code))
                else None
            ),
            form_component=item.component if (item := form_by.get(code)) else None,
            quali_race=(
                _estimate(trend.delta, trend.interval_low, trend.interval_high, trend.events)
                if (trend := quali_by.get(code))
                else None
            ),
            wet=(
                _estimate(
                    rain.shrunk_delta, rain.interval_low, rain.interval_high, rain.wet_sessions
                )
                if (rain := wet_by.get(code))
                else None
            ),
            pace=_samples(history.filter(pl.col("driver_code") == code)),
            teammate=_teammates(pairs, code),
        )
        for code in sorted(seats)
    ]
    return DriverView(
        generated_at=metrics.generated_at,
        run_id=metrics.run_id,
        as_of=metrics.as_of,
        event=metrics.context,
        half_life_events=metrics.form.half_life_events,
        drivers=drivers,
    )


def _teammates(pairs: pl.DataFrame, code: str) -> list[TeammateSample]:
    found: list[TeammateSample] = []
    for side, other, sign in (("driver_a", "driver_b", 1.0), ("driver_b", "driver_a", -1.0)):
        for row in pairs.filter(pl.col(side) == code).iter_rows(named=True):
            found.append(
                TeammateSample(
                    season=int(row["season"]),
                    round=int(row["round"]),
                    race_date=row["race_date"],
                    circuit_id=str(row["circuit_id"]),
                    teammate=str(row[other]),
                    delta=sign * float(row["delta"]),
                )
            )
    return sorted(found, key=lambda item: (item.race_date, item.teammate))


def track_view(
    assembled: Assembled, profiles: dict[str, CircuitProfile] | None = None
) -> TrackView:
    metrics = assembled.metrics
    known = profiles or load_circuits()
    circuit = metrics.context.circuit_id
    history = assembled.pace.filter(
        (pl.col("race_date") < metrics.as_of) & (pl.col("circuit_id") == circuit)
    )
    regression = {item.constructor_id: item for item in metrics.track.regression}
    similarity = {item.constructor_id: item for item in metrics.track.similarity}
    teams = sorted(set(regression) | set(similarity))
    return TrackView(
        generated_at=metrics.generated_at,
        run_id=metrics.run_id,
        as_of=metrics.as_of,
        event=metrics.context,
        profile=known[circuit],
        neighbours=metrics.track.neighbours,
        teams=[
            TeamAtTrack(
                constructor_id=team,
                regression=(
                    _estimate(item.estimate, item.interval_low, item.interval_high, item.samples)
                    if (item := regression.get(team))
                    else None
                ),
                similarity=(
                    _estimate(
                        close.estimate, close.interval_low, close.interval_high, close.samples
                    )
                    if (close := similarity.get(team))
                    else None
                ),
                disagree=team in metrics.track.disagreements,
                history=_samples(history.filter(pl.col("constructor_id") == team)),
            )
            for team in teams
        ],
        dropped_reprofiled=metrics.track.dropped_reprofiled,
    )
