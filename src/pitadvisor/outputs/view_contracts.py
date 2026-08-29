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
from pitadvisor.model import backtest
from pitadvisor.model.backtest import Assumption
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


class PipelineDiagnostic(BaseModel, frozen=True):
    name: str
    table: str
    value: int
    detail: str


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
    diagnostics: list[PipelineDiagnostic]
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
        diagnostics=[PipelineDiagnostic(**item.model_dump()) for item in report.diagnostics],
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
    # NaN is not JSON and the browser refuses to parse it, so a number that went non-finite
    # upstream fails here rather than blanking a page
    return store.put(view_key(name), json.dumps(payload, indent=2, allow_nan=False).encode())


class Estimate(BaseModel, frozen=True):
    """§6.2: nothing reaches the page as a bare number. The interval travels with it."""

    value: float
    low: float
    high: float
    samples: int = 0


class WeekendDriver(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    # the lake holds five seasons of drivers, so a weekend page has to be able to say which of
    # them is still in a car without the frontend guessing from a name it recognises
    last_season: int
    last_race_date: date
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
    last_season: int
    last_race_date: date
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


def _last_seen(pace: pl.DataFrame) -> dict[str, tuple[int, date]]:
    ordered = pace.sort("race_date")
    return {
        str(row["driver_code"]): (int(row["season"]), row["race_date"])
        for row in ordered.select("driver_code", "season", "race_date").iter_rows(named=True)
    }


def weekend_view(assembled: Assembled) -> WeekendView:
    metrics = assembled.metrics
    seats = _seats(assembled.pace)
    last_seen = _last_seen(assembled.pace)
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
            last_season=last_seen[code][0],
            last_race_date=last_seen[code][1],
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
    last_seen = _last_seen(history)
    form_by = {item.driver_code: item for item in metrics.form.drivers}
    quali_by = {item.driver_code: item for item in metrics.quali.drivers}
    wet_by = {item.key: item for item in metrics.wet.drivers}
    pairs = form_pairings(history.filter(~pl.col("is_wet")))

    drivers = [
        DriverHistory(
            driver_code=code,
            constructor_id=seats[code],
            last_season=last_seen[code][0],
            last_race_date=last_seen[code][1],
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


class ScenarioShare(BaseModel, frozen=True):
    scenario: str
    weight: float
    paths: int


class ForecastDriver(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    grid: int
    win: float
    podium: float
    points: float
    finish: float
    expected_position: float
    # the tenth and ninetieth of the simulated finishing order. §6.2 wants the spread on the
    # page, and a single expected position hides the whole point of running paths
    position_low: int
    position_high: int
    position: list[float]


class ScenarioDriver(BaseModel, frozen=True):
    scenario: str
    driver_code: str
    win: float
    podium: float
    points: float


class Bounds(BaseModel, frozen=True):
    """A score with the interval the race-level bootstrap gave it. One race cannot be
    resampled, so the bounds are allowed to be absent rather than faked as zero width."""

    value: float
    low: float | None
    high: float | None
    races: int
    draws: int


def _bounds(interval: backtest.Interval, races: int) -> Bounds:
    finite = math.isfinite(interval.low) and math.isfinite(interval.high)
    return Bounds(
        value=interval.value,
        low=interval.low if finite else None,
        high=interval.high if finite else None,
        races=races,
        draws=interval.draws,
    )


class Evidence(BaseModel, frozen=True):
    """What the backtest said about this forecast, carried next to it. A probability with no
    score attached is the thing this project exists not to ship."""

    holdout: int
    races: int
    from_season: int
    log_loss: dict[str, Bounds]
    brier: dict[str, Bounds]
    beats_baselines: bool
    separated_from: list[str]


class ForecastView(BaseModel, frozen=True):
    view: str = "forecast_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    as_of: date
    event: EventContext
    paths: int
    laps: int
    scenarios: list[ScenarioShare]
    weights_are_forecast: bool
    drivers: list[ForecastDriver]
    by_scenario: list[ScenarioDriver]
    assumptions: list[Assumption]
    evidence: Evidence | None


def _quantile(row: list[float], share: float) -> int:
    running = 0.0
    for index, value in enumerate(row, start=1):
        running += value
        if running >= share:
            return index
    return len(row)


def forecast_view(
    predicted: backtest.Forecast,
    context: EventContext,
    run_id: str,
    seats: dict[str, str],
    grid: dict[str, int],
    evidence: Evidence | None = None,
    generated_at: datetime | None = None,
) -> ForecastView:
    outcome = predicted.outcome
    rows = [
        ForecastDriver(
            driver_code=code,
            constructor_id=seats.get(code, "unknown"),
            grid=grid.get(code, len(outcome.driver_code)),
            win=outcome.win[index],
            podium=outcome.podium[index],
            points=outcome.points[index],
            finish=outcome.finish[index],
            expected_position=outcome.expected_position[index],
            position_low=_quantile(outcome.position[index], 0.1),
            position_high=_quantile(outcome.position[index], 0.9),
            position=outcome.position[index],
        )
        for index, code in enumerate(outcome.driver_code)
    ]
    return ForecastView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        as_of=predicted.as_of,
        event=context,
        paths=predicted.paths,
        laps=predicted.laps,
        scenarios=[
            ScenarioShare(
                scenario=name,
                weight=predicted.scenario_weights.get(name, 0.0),
                paths=predicted.scenarios[name].paths if name in predicted.scenarios else 0,
            )
            for name in backtest.SCENARIOS
        ],
        weights_are_forecast=predicted.weights_are_forecast,
        drivers=sorted(rows, key=lambda item: item.expected_position),
        by_scenario=[
            ScenarioDriver(
                scenario=name,
                driver_code=code,
                win=run.win[index],
                podium=run.podium[index],
                points=run.points[index],
            )
            for name, run in predicted.scenarios.items()
            for index, code in enumerate(run.driver_code)
        ],
        assumptions=predicted.assumptions,
        evidence=evidence,
    )


class CurvePoint(BaseModel, frozen=True):
    low: float
    high: float
    forecast: float
    observed: float
    count: int


class ScoredModel(BaseModel, frozen=True):
    name: str
    rows: int
    races: int
    log_loss: Bounds
    brier: Bounds
    calibration: dict[str, float]
    curves: dict[str, list[CurvePoint]]


class PairedGain(BaseModel, frozen=True):
    """How much better than one baseline, resampled over the same races. Two marginal
    intervals that overlap say nothing about a difference measured on the same events."""

    baseline: str
    log_loss_gain: Bounds
    brier_gain: Bounds


class RaceLoss(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    race_date: date
    starters: int
    log_loss: dict[str, float]


class CalibrationView(BaseModel, frozen=True):
    view: str = "calibration_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    from_season: int
    holdout: int
    paths: int
    seed: int
    field: int
    model_name: str
    events: list[str]
    scored: list[ScoredModel]
    per_race: list[RaceLoss]
    beats_baselines: bool
    separated_from: list[str]
    paired: list[PairedGain]
    assumptions: list[Assumption]


def calibration_view(
    report: backtest.Report, generated_at: datetime | None = None
) -> CalibrationView:
    return CalibrationView(
        generated_at=generated_at or report.generated_at,
        run_id=report.run_id,
        from_season=report.from_season,
        holdout=report.holdout,
        paths=report.paths,
        seed=report.seed,
        field=report.field,
        model_name=backtest.MODEL,
        events=list(backtest.EVENTS),
        scored=[
            ScoredModel(
                name=item.name,
                rows=item.rows,
                races=item.races,
                log_loss=_bounds(item.log_loss, item.races),
                brier=_bounds(item.brier, item.races),
                calibration=item.calibration,
                curves={
                    event: [CurvePoint(**point.model_dump()) for point in bins]
                    for event, bins in item.curves.items()
                },
            )
            for item in report.scored
        ],
        per_race=[RaceLoss(**item.model_dump()) for item in report.per_race],
        beats_baselines=report.beats_baselines,
        separated_from=report.separated_from,
        paired=[
            PairedGain(
                baseline=item.baseline,
                log_loss_gain=_bounds(item.log_loss_gain, len(report.per_race)),
                brier_gain=_bounds(item.brier_gain, len(report.per_race)),
            )
            for item in report.paired
        ],
        assumptions=report.assumptions,
    )


def evidence_from(report: backtest.Report) -> Evidence:
    ours = next(item for item in report.scored if item.name == backtest.MODEL)
    return Evidence(
        holdout=report.holdout,
        races=ours.races,
        from_season=report.from_season,
        log_loss={item.name: _bounds(item.log_loss, item.races) for item in report.scored},
        brier={item.name: _bounds(item.brier, item.races) for item in report.scored},
        beats_baselines=report.beats_baselines,
        separated_from=report.separated_from,
    )
