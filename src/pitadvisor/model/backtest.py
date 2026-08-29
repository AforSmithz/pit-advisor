# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
from pydantic import BaseModel

from pitadvisor.features import form, quali_race, reliability, track_fit
from pitadvisor.features import weather as wet_weather
from pitadvisor.features.assemble import (
    EventContext,
    NoEventError,
    pace_frame,
    quali_events,
    quali_frame,
    races,
    session_paces,
    team_frame,
)
from pitadvisor.features.clean_pace import Reason, Regime, SessionPace, classify, is_green
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.model import baselines
from pitadvisor.model.metrics import Bin, Interval, brier, log_loss, race_bootstrap
from pitadvisor.model.metrics import reliability as curve
from pitadvisor.quality.checks import read_table
from pitadvisor.sim import dnf, overtake, race, safety_car, starts, tyres
from pitadvisor.types import Layer, SessionKind

FIELD = baselines.FIELD
SCENARIOS = ("dry", "mixed", "wet")
# a mixed race is run on a track that is drying or wetting, so a driver's wet advantage
# counts for half of what it does in standing water
MIXED_WET_SHARE = 0.5
# and the field spends more of it behind a safety car than it does on a dry Sunday
MIXED_SAFETY_CAR = 1.6
WET_SAFETY_CAR = 2.2
# how far a driver's Sunday drifts from his rating, over and above the rating's own error.
# measured from the pace history rather than asserted
DEFAULT_RACE_DAY_SD = 0.35
DEFAULT_QUALI_ANCHOR_SD = 0.5
PRIOR_REGIME_RACES = 6.0


class NoForecastError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


@dataclass(frozen=True)
class Panel:
    """Everything the lake holds, read and fitted once. Nothing in here depends on a
    prediction date: the per-session pace fits use only their own session's laps, so walking
    the calendar forward is a matter of filtering, not of refitting 228 regressions a race."""

    events: pl.DataFrame
    results: pl.DataFrame
    paces: list[SessionPace]
    pace: pl.DataFrame
    quali: pl.DataFrame
    quali_events: list[tuple[date, quali_race.EventQualiRace]]
    starts: pl.DataFrame
    cautions: pl.DataFrame
    passes: pl.DataFrame
    traffic: pl.DataFrame
    stops: pl.DataFrame
    degradation: pl.DataFrame
    benchmarks: pl.DataFrame
    regimes: pl.DataFrame
    laps: dict[tuple[int, int], int]
    entries: dict[tuple[int, int], baselines.Entries]
    race_day_sd: float
    quali_anchor_sd: float


def panel(store: ObjectStore, layer: Layer = Layer.BRONZE) -> Panel:
    events = races(store, layer)
    results = read_table(store, layer, "results")
    laps = read_table(store, layer, "session_laps")
    qualifying = read_table(store, layer, "qualifying")
    if results is None or laps is None or qualifying is None:
        raise NoEventError("results, qualifying and session_laps must all be in bronze")
    racing = laps.filter(pl.col("session") == str(SessionKind.RACE))
    paces, _ = session_paces(store, layer=layer)
    pace = pace_frame(paces, results, events)
    if not pace.height:
        raise NoEventError("no clean pace to stand on, run 'pitadv backfill --source fastf1'")

    quali = quali_frame(qualifying, events)
    stacked = quali_events(quali, paces)
    dated = events.select("season", "round", "race_date", "circuit_id")
    outcome = results.join(dated, on=["season", "round"]).with_columns(
        pl.col("points").cast(pl.Float64)
    )
    return Panel(
        events=events,
        results=outcome,
        paces=paces,
        pace=pace,
        quali=quali,
        quali_events=stacked,
        starts=_start_frame(racing, outcome),
        cautions=_caution_frame(racing, dated),
        passes=_pass_frame(racing, dated, pace),
        traffic=_traffic_frame(racing, dated),
        stops=_stop_frame(racing, dated, outcome),
        degradation=_degradation_frame(paces, dated),
        benchmarks=_benchmark_frame(paces, dated),
        regimes=_regime_frame(paces, dated),
        laps={
            (int(row["season"]), int(row["round"])): int(row["lap"])
            for row in racing.group_by("season", "round")
            .agg(pl.col("lap").max())
            .iter_rows(named=True)
        },
        entries=baselines.all_entries(outcome.select(baselines.COLUMNS)),
        race_day_sd=_race_day_sd(pace),
        quali_anchor_sd=_quali_anchor_sd(stacked),
    )


def _start_frame(racing: pl.DataFrame, results: pl.DataFrame) -> pl.DataFrame:
    opening = racing.filter(pl.col("lap") == 1).select(
        "season", "round", "driver_code", pl.col("position").alias("start_position")
    )
    return (
        results.drop_nulls("driver_code")
        .filter(pl.col("grid") > 0)
        .select("season", "round", "race_date", "driver_code", "grid")
        .join(opening, on=["season", "round", "driver_code"], how="inner")
        .drop_nulls("start_position")
        .select(starts.COLUMNS)
    )


def _caution_frame(racing: pl.DataFrame, dated: pl.DataFrame) -> pl.DataFrame:
    # fastf1 concatenates one digit per marshal sector, so a 4 anywhere in the string is a
    # sector that saw the safety car
    flagged = racing.group_by("season", "round", "lap").agg(
        pl.col("track_status")
        .drop_nulls()
        .str.contains("4")
        .any()
        .fill_null(False)
        .alias("safety_car")
    )
    return flagged.join(dated, on=["season", "round"]).select(safety_car.COLUMNS)


def _pass_frame(racing: pl.DataFrame, dated: pl.DataFrame, pace: pl.DataFrame) -> pl.DataFrame:
    duels = overtake.pairs(racing)
    if not duels.height:
        return pl.DataFrame(schema={name: pl.Float64 for name in overtake.COLUMNS})
    dry = pace.filter(~pl.col("is_wet")).select("season", "round", "driver_code", "value")
    return (
        duels.join(dated, on=["season", "round"])
        .join(
            dry.rename({"driver_code": "ahead", "value": "ahead_pace"}),
            on=["season", "round", "ahead"],
            how="left",
        )
        .join(
            dry.rename({"driver_code": "behind", "value": "behind_pace"}),
            on=["season", "round", "behind"],
            how="left",
        )
        .with_columns((pl.col("ahead_pace") - pl.col("behind_pace")).alias("delta"))
        .select(overtake.COLUMNS)
    )


def _traffic_frame(racing: pl.DataFrame, dated: pl.DataFrame) -> pl.DataFrame:
    """What a lap in dirty air costs, per driver per race: the same exclusion the pace fit
    throws away, kept this time because the simulation needs to know what it is worth."""
    found: list[pl.DataFrame] = []
    for _, event in racing.group_by("season", "round"):
        marked = classify(event).filter(pl.col("lap_time_millis").is_not_null())
        summary = marked.group_by("season", "round", "driver_code").agg(
            pl.col("lap_time_millis")
            .filter(pl.col("exclusion") == str(Reason.TRAFFIC))
            .median()
            .alias("dirty"),
            pl.col("lap_time_millis").filter(pl.col("exclusion").is_null()).median().alias("clean"),
        )
        found.append(summary)
    if not found:
        return pl.DataFrame(schema={name: pl.Float64 for name in overtake.TRAFFIC_COLUMNS})
    return (
        pl.concat(found)
        .drop_nulls(["dirty", "clean"])
        .join(dated, on=["season", "round"])
        .with_columns((pl.col("dirty") - pl.col("clean")).alias("penalty_millis"))
        .filter(pl.col("penalty_millis") > 0)
        .select(overtake.TRAFFIC_COLUMNS)
    )


def _stop_frame(racing: pl.DataFrame, dated: pl.DataFrame, results: pl.DataFrame) -> pl.DataFrame:
    """A stop costs an in-lap and an out-lap, both measured against the same driver's own
    green pace, which is the only reference that does not import the field's spread."""
    reference = (
        racing.filter(
            is_green(pl.col("track_status"))
            & pl.col("lap_time_millis").is_not_null()
            & ~pl.col("pit_in")
            & ~pl.col("pit_out")
        )
        .group_by("season", "round", "driver_code")
        .agg(pl.col("lap_time_millis").median().alias("own_millis"))
    )
    total = racing.group_by("season", "round").agg(pl.col("lap").max().alias("race_laps"))
    starters = (
        results.drop_nulls("driver_code")
        .group_by("season", "round")
        .agg(pl.len().alias("starters"))
    )
    entering = racing.filter(pl.col("pit_in")).select(
        "season", "round", "driver_code", "lap", "lap_time_millis"
    )
    leaving = racing.filter(pl.col("pit_out")).select(
        "season",
        "round",
        "driver_code",
        (pl.col("lap") - 1).alias("lap"),
        pl.col("lap_time_millis").alias("out_millis"),
    )
    joined = (
        entering.join(leaving, on=["season", "round", "driver_code", "lap"], how="left")
        .join(reference, on=["season", "round", "driver_code"], how="left")
        .join(total, on=["season", "round"])
        .join(starters, on=["season", "round"])
        .join(dated, on=["season", "round"])
    )
    return (
        joined.sort("season", "round", "driver_code", "lap")
        .with_columns(
            (pl.col("lap").cum_count().over("season", "round", "driver_code")).alias("stop"),
            (pl.col("lap") / pl.col("race_laps")).alias("fraction"),
            (pl.col("lap_time_millis") + pl.col("out_millis") - 2 * pl.col("own_millis")).alias(
                "excess_millis"
            ),
        )
        .select(tyres.COLUMNS)
    )


def _degradation_frame(paces: list[SessionPace], dated: pl.DataFrame) -> pl.DataFrame:
    rows = [
        {"season": item.season, "round": item.round, "millis_per_lap": item.b_tyre_millis}
        for item in paces
        if item.regime is Regime.DRY
    ]
    if not rows:
        return pl.DataFrame(schema={name: pl.Float64 for name in tyres.DEGRADATION_COLUMNS})
    return pl.DataFrame(rows).join(dated, on=["season", "round"]).select(tyres.DEGRADATION_COLUMNS)


def _benchmark_frame(paces: list[SessionPace], dated: pl.DataFrame) -> pl.DataFrame:
    rows = [
        {
            "season": item.season,
            "round": item.round,
            "regime": str(item.regime),
            "benchmark_millis": item.benchmark_millis,
        }
        for item in paces
    ]
    if not rows:
        schema = {
            "season": pl.Int64,
            "round": pl.Int64,
            "race_date": pl.Date,
            "circuit_id": pl.String,
            "regime": pl.String,
            "benchmark_millis": pl.Float64,
        }
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows).join(dated, on=["season", "round"])


def _regime_frame(paces: list[SessionPace], dated: pl.DataFrame) -> pl.DataFrame:
    """Which weather a race was actually run in, read off which regimes produced a fit."""
    seen: dict[tuple[int, int], set[str]] = {}
    for item in paces:
        seen.setdefault((item.season, item.round), set()).add(str(item.regime))
    rows = [
        {
            "season": season,
            "round": round_,
            "scenario": "mixed"
            if regimes == {"dry", "wet"}
            else ("wet" if regimes == {"wet"} else "dry"),
        }
        for (season, round_), regimes in seen.items()
    ]
    if not rows:
        return pl.DataFrame(
            schema={
                "season": pl.Int64,
                "round": pl.Int64,
                "race_date": pl.Date,
                "circuit_id": pl.String,
                "scenario": pl.String,
            }
        )
    return pl.DataFrame(rows).join(dated, on=["season", "round"])


def _quali_anchor_sd(stacked: list[tuple[date, quali_race.EventQualiRace]]) -> float:
    """How far a driver's quali-to-race conversion moves from one weekend to the next. It is
    what the anchor does not know, and it is what decides how much weight the anchor gets."""
    seen: dict[str, list[float]] = {}
    for _, event in stacked:
        for driver in event.drivers:
            if driver.delta is not None:
                seen.setdefault(driver.driver_code, []).append(driver.delta)
    spreads = [float(np.std(values, ddof=1)) for values in seen.values() if len(values) >= 5]
    if not spreads:
        return DEFAULT_QUALI_ANCHOR_SD
    return float(np.median(spreads))


def _race_day_sd(pace: pl.DataFrame) -> float:
    """How much a driver's own Sunday moves around his own average, in percent of a lap."""
    dry = pace.filter(~pl.col("is_wet"))
    spread = (
        dry.group_by("driver_code")
        .agg(pl.col("value").std().alias("sd"), pl.len().alias("races"))
        .filter(pl.col("races") >= 5)
        .drop_nulls("sd")
    )
    if not spread.height:
        return DEFAULT_RACE_DAY_SD
    return float(np.median(spread["sd"].to_numpy().astype(float)))


class Assumption(BaseModel, frozen=True):
    name: str
    value: float
    detail: str


class Forecast(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    race_date: date
    as_of: date
    paths: int
    laps: int
    scenario_weights: dict[str, float]
    weights_are_forecast: bool
    outcome: race.Outcome
    scenarios: dict[str, race.Outcome]
    assumptions: list[Assumption]


def seats_for(
    pane: Panel, context: EventContext, as_of: date
) -> tuple[dict[str, str], dict[str, str]]:
    entered = pane.results.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).drop_nulls("driver_code")
    if not entered.height:
        # the event has not been run, so the entry list is whoever was last in each car
        entered = (
            pane.results.filter(pl.col("race_date") < as_of)
            .drop_nulls("driver_code")
            .sort("race_date")
            .group_by("constructor_id", "driver_code")
            .last()
        )
    seats = {
        str(row["driver_code"]): str(row["constructor_id"]) for row in entered.iter_rows(named=True)
    }
    ids = {str(row["driver_code"]): str(row["driver_id"]) for row in entered.iter_rows(named=True)}
    return seats, ids


def grid_for(pane: Panel, context: EventContext, codes: list[str]) -> dict[str, int]:
    entered = pane.results.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).drop_nulls("driver_code")
    known = {
        str(row["driver_code"]): (int(row["grid"]) if int(row["grid"]) > 0 else FIELD)
        for row in entered.iter_rows(named=True)
    }
    # a car with no grid slot starts from the back, which is where a pit lane start begins
    return {code: known.get(code, FIELD) for code in codes}


def _reference_millis(pane: Panel, circuit_id: str, as_of: date) -> float:
    history = pane.benchmarks.filter(
        (pl.col("race_date") < as_of) & (pl.col("regime") == str(Regime.DRY))
    )
    here = history.filter(pl.col("circuit_id") == circuit_id)
    chosen = here if here.height else history
    if not chosen.height:
        raise NoForecastError(f"no dry benchmark to scale {circuit_id} against before {as_of}")
    recent = chosen.sort("race_date").tail(3)["benchmark_millis"].to_numpy()
    return float(np.median(recent.astype(float)))


def scenario_weights(pane: Panel, circuit_id: str, as_of: date) -> dict[str, float]:
    """Climatology, not the archive. The observed weather of a race being backtested is the
    single most tempting leak in this phase: it is in the lake, it is keyed by the event, and
    using it would let the model know it rained before the race it is predicting."""
    history = pane.regimes.filter(pl.col("race_date") < as_of)
    field = np.array([0.75, 0.15, 0.10])
    if history.height:
        counted = np.array([float((history["scenario"] == name).sum()) for name in SCENARIOS])
        field = counted / counted.sum()
    here = history.filter(pl.col("circuit_id") == circuit_id)
    counted = np.array([float((here["scenario"] == name).sum()) for name in SCENARIOS])
    mixed = counted + field * PRIOR_REGIME_RACES
    return {name: float(value) for name, value in zip(SCENARIOS, mixed / mixed.sum(), strict=True)}


def setup(
    pane: Panel,
    context: EventContext,
    as_of: date,
    scenario: str,
    grid: dict[str, int] | None = None,
) -> race.RaceSetup:
    history = pane.pace.filter(pl.col("race_date") < as_of)
    if not history.height:
        raise NoForecastError(f"no pace history before {as_of}")
    seats, ids = seats_for(pane, context, as_of)
    if not seats:
        raise NoForecastError(f"no entry list for {context.season} round {context.round}")

    dry = history.filter(~pl.col("is_wet"))
    shape = form.fit(dry.select(form.COLUMNS), as_of)
    track = track_fit.fit(team_frame(history), context.circuit_id, as_of)
    rain = wet_weather.wet_form(history.select(wet_weather.COLUMNS), as_of)
    hazard = reliability.fit(
        pane.results.filter(pl.col("race_date") < as_of).select(reliability.COLUMNS), as_of
    )
    reference = _reference_millis(pane, context.circuit_id, as_of)
    codes = sorted(seats)
    slots = grid or grid_for(pane, context, codes)

    percent, error = _ratings(shape, track, rain, seats, codes, scenario, pane.race_day_sd)
    percent, error = _anchored(pane, context, as_of, codes, percent, error, scenario)
    drivers = [
        race.Driver(
            driver_code=code,
            constructor_id=seats[code],
            grid=slots.get(code, FIELD),
            pace_millis=reference * (1.0 + percent[code] / 100.0),
            pace_sd_millis=reference * error[code] / 100.0,
        )
        for code in codes
    ]
    caution = safety_car.fit(pane.cautions, context.circuit_id, as_of)
    if scenario != "dry":
        multiple = MIXED_SAFETY_CAR if scenario == "mixed" else WET_SAFETY_CAR
        caution = caution.model_copy(update={"per_lap": min(caution.per_lap * multiple, 0.5)})
    return race.RaceSetup(
        season=context.season,
        round=context.round,
        circuit_id=context.circuit_id,
        race_date=context.race_date,
        laps=_race_laps(pane, context, as_of),
        reference_millis=reference,
        drivers=drivers,
        start=starts.fit(pane.starts, as_of),
        tyre=tyres.fit(pane.stops, pane.degradation, context.circuit_id, as_of),
        passing=overtake.fit(pane.passes, context.circuit_id, as_of, traffic=pane.traffic),
        safety_car=caution,
        retirement=dnf.build(hazard, seats, ids),
    )


def _race_laps(pane: Panel, context: EventContext, as_of: date) -> int:
    """How long the race is scheduled to be, taken from what this circuit has run before.
    The distance this particular race actually went is not known until it has been run: a
    red flag shortens it, and reading that off the result would be a leak."""
    before = pane.events.filter(
        (pl.col("circuit_id") == context.circuit_id) & (pl.col("race_date") < as_of)
    )
    known = [
        pane.laps[(int(row["season"]), int(row["round"]))]
        for row in before.iter_rows(named=True)
        if (int(row["season"]), int(row["round"])) in pane.laps
    ]
    if known:
        return int(np.median(known))
    if pane.laps:
        return int(np.median(list(pane.laps.values())))
    return 57


def _ratings(
    shape: form.FormFit,
    track: track_fit.TrackFit,
    rain: wet_weather.WetForm,
    seats: dict[str, str],
    codes: list[str],
    scenario: str,
    race_day_sd: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Team level from the track fit, driver level from the teammate contrasts. The form
    effects are centred inside each team first: they are a within-team contrast, and adding
    them raw would move the car's level as well as the driver's."""
    regression = {item.constructor_id: item for item in track.regression}
    similarity = {item.constructor_id: item for item in track.similarity}
    teams = sorted(set(seats.values()))
    levels: dict[str, float] = {}
    level_error: dict[str, float] = {}
    for team in teams:
        chosen = regression.get(team) or similarity.get(team)
        if chosen is None:
            continue
        levels[team] = chosen.estimate
        level_error[team] = chosen.standard_error
    field_level = float(np.mean(list(levels.values()))) if levels else 0.0
    field_error = float(np.mean(list(level_error.values()))) if level_error else 1.0

    effects = {item.driver_code: item for item in shape.drivers}
    centred: dict[str, float] = {}
    for team in teams:
        mates = [code for code in codes if seats[code] == team and code in effects]
        middle = float(np.mean([effects[code].effect for code in mates])) if mates else 0.0
        for code in mates:
            centred[code] = effects[code].effect - middle

    wet_driver = {item.key: item for item in rain.drivers}
    wet_team = {item.key: item for item in rain.teams}
    share = {"dry": 0.0, "mixed": MIXED_WET_SHARE, "wet": 1.0}[scenario]

    percent: dict[str, float] = {}
    error: dict[str, float] = {}
    for code in codes:
        team = seats[code]
        value = levels.get(team, field_level) + centred.get(code, 0.0)
        spread = level_error.get(team, field_error)
        if code in effects:
            spread = float(np.hypot(spread, effects[code].standard_error))
        if share:
            wet = wet_driver.get(code) or wet_team.get(team)
            if wet is not None:
                value += share * wet.shrunk_delta
                spread = float(np.hypot(spread, share * wet.standard_error))
        percent[code] = value
        error[code] = float(np.hypot(spread, race_day_sd))
    return percent, error


def _anchored(
    pane: Panel,
    context: EventContext,
    as_of: date,
    codes: list[str],
    percent: dict[str, float],
    error: dict[str, float],
    scenario: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """This weekend's qualifying is the freshest reading of the car there is, and §5.3 exists
    to convert it: a driver's race pace is his quali gap less his own quali-to-race delta.
    It is combined with the standing rating by precision, not by a weight anyone chose."""
    session = pane.quali.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    )
    if not session.height:
        return percent, error
    try:
        current = quali_race.fit_event(session)
    except quali_race.NoQualifyingLapError:
        return percent, error
    history = [item for item in pane.quali_events if item[0] < as_of]
    if not history:
        return percent, error
    trend = quali_race.trend(history, as_of)
    deltas = {item.driver_code: item for item in trend.drivers}
    field_delta = float(np.mean([item.delta for item in trend.drivers])) if trend.drivers else 0.0
    field_error = (
        float(np.mean([item.standard_error for item in trend.drivers])) if trend.drivers else 1.0
    )
    # in the wet the dry quali order stops being a reading of race pace, so the anchor is
    # widened rather than dropped: it still says which cars are quick, less certainly
    widen = {"dry": 1.0, "mixed": 1.6, "wet": 2.4}[scenario]

    blended: dict[str, float] = dict(percent)
    spread: dict[str, float] = dict(error)
    for driver in current.drivers:
        code = driver.driver_code
        if code not in percent:
            continue
        own = deltas.get(code)
        delta = own.delta if own else field_delta
        delta_error = own.standard_error if own else field_error
        anchor = driver.quali_percent_off - delta
        anchor_var = (np.hypot(delta_error, pane.quali_anchor_sd) * widen) ** 2
        rating_var = error[code] ** 2
        if anchor_var <= 0.0 or rating_var <= 0.0:
            continue
        precision = 1.0 / anchor_var + 1.0 / rating_var
        blended[code] = (anchor / anchor_var + percent[code] / rating_var) / precision
        spread[code] = float(np.sqrt(1.0 / precision))
    return blended, spread


def forecast(
    pane: Panel,
    context: EventContext,
    as_of: date,
    rng: np.random.Generator,
    paths: int = race.PATHS,
    weights: dict[str, float] | None = None,
    weights_are_forecast: bool = False,
    grid: dict[str, int] | None = None,
) -> Forecast:
    mix = weights or scenario_weights(pane, context.circuit_id, as_of)
    outcomes: dict[str, race.Outcome] = {}
    built: dict[str, race.RaceSetup] = {}
    for name in SCENARIOS:
        if mix.get(name, 0.0) <= 0.0:
            continue
        built[name] = setup(pane, context, as_of, name, grid)
        outcomes[name] = race.simulate(built[name], rng, paths, name)
    if not outcomes:
        raise NoForecastError("every scenario carries zero weight")
    blended = race.blend(outcomes, mix, "blended")
    reference = built[next(iter(built))]
    return Forecast(
        season=context.season,
        round=context.round,
        circuit_id=context.circuit_id,
        race_date=context.race_date,
        as_of=as_of,
        paths=paths,
        laps=reference.laps,
        scenario_weights=mix,
        weights_are_forecast=weights_are_forecast,
        outcome=blended,
        scenarios=outcomes,
        assumptions=_assumptions(reference, pane),
    )


def _assumptions(built: race.RaceSetup, pane: Panel) -> list[Assumption]:
    return [
        Assumption(
            name="laps",
            value=float(built.laps),
            detail="race distance the simulation runs",
        ),
        Assumption(
            name="reference_lap_millis",
            value=built.reference_millis,
            detail="dry benchmark the percentage ratings are scaled against",
        ),
        Assumption(
            name="degradation_millis_per_lap",
            value=built.tyre.degradation_millis,
            detail=f"shrunk from {built.tyre.events_used} races, field "
            f"{built.tyre.field_degradation_millis:.0f}",
        ),
        Assumption(
            name="pit_loss_millis",
            value=built.tyre.pit_loss_millis,
            detail="in-lap plus out-lap against the driver's own green pace",
        ),
        Assumption(
            name="safety_car_per_green_lap",
            value=built.safety_car.per_lap,
            detail=f"field rate {built.safety_car.field_per_lap:.4f}, "
            f"period {built.safety_car.mean_period_laps:.1f} laps",
        ),
        Assumption(
            name="pass_probability_at_parity",
            value=float(1.0 / (1.0 + np.exp(-built.passing.base))),
            detail=f"within {built.passing.attempts:.0f} weighted attempts inside striking "
            f"range, slope {built.passing.slope:.2f} per percent",
        ),
        Assumption(
            name="dirty_air_millis",
            value=built.passing.dirty_air_millis,
            detail="what a lap inside the following gap costs against the driver's own clean "
            f"pace, field {built.passing.field_dirty_air_millis:.0f}",
        ),
        Assumption(
            name="race_day_sd_percent",
            value=pane.race_day_sd,
            detail="how far a driver's Sunday drifts from his own rating",
        ),
    ]


MODEL = "simulation"
EVENTS = ("win", "podium", "points")
EVENT_CUTOFF = {"win": 1, "podium": 3, "points": 10}


class Scored(BaseModel, frozen=True):
    name: str
    rows: int
    races: int
    log_loss: Interval
    brier: Interval
    calibration: dict[str, float]
    curves: dict[str, list[Bin]]


class Paired(BaseModel, frozen=True):
    """Two marginal intervals that overlap do not say the difference is uncertain, because
    both are scored on the same races. The difference gets its own interval, resampled over
    the same races, which is the comparison anyone should actually read."""

    baseline: str
    log_loss_gain: Interval
    brier_gain: Interval


class RaceScore(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    race_date: date
    starters: int
    log_loss: dict[str, float]


class Report(BaseModel, frozen=True):
    generated_at: datetime
    run_id: str
    from_season: int
    holdout: int
    paths: int
    field: int
    seed: int
    scored: list[Scored]
    paired: list[Paired]
    per_race: list[RaceScore]
    beats_baselines: bool
    assumptions: list[Assumption]


def _renormalised(grid: np.ndarray, starters: int) -> np.ndarray:
    """A twenty class forecast on a nineteen car race puts mass on a position that cannot
    happen. Moving it back onto the positions that can is fairer to every predictor, model
    and baseline alike. The array stays FIELD wide so races of different sizes still stack."""
    kept = min(starters, FIELD)
    trimmed = np.array(grid[:, :kept], dtype=float)
    total = trimmed.sum(axis=1, keepdims=True)
    padded = np.zeros((grid.shape[0], FIELD))
    padded[:, :kept] = np.divide(trimmed, np.where(total > 0.0, total, 1.0))
    return padded


def run(
    pane: Panel,
    from_season: int,
    holdout: int,
    rng: np.random.Generator,
    run_id: str,
    paths: int = race.PATHS,
    seed: int = 0,
    generated_at: datetime | None = None,
) -> Report:
    """Time forward, one race at a time. Every fit behind a prediction sees only what had
    happened when it was made, which is why the panel is filtered by date and never shuffled."""
    calendar = pane.events.filter(pl.col("season") >= from_season).sort("race_date").tail(holdout)
    if not calendar.height:
        raise NoForecastError(f"no races from {from_season} to hold out")
    entries = pane.results.select(baselines.COLUMNS)

    grids: dict[str, list[np.ndarray]] = {MODEL: [], **{name: [] for name in baselines.FEATURES}}
    truth: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    per_race: list[RaceScore] = []
    assumptions: list[Assumption] = []

    for index, row in enumerate(calendar.iter_rows(named=True)):
        context = EventContext(
            season=int(row["season"]),
            round=int(row["round"]),
            circuit_id=str(row["circuit_id"]),
            race_name=str(row["race_name"]),
            race_date=row["race_date"],
            start_utc=row["start_utc"],
        )
        finished = _finishers(pane, context)
        if not finished:
            continue
        try:
            predicted = forecast(pane, context, context.race_date, rng, paths)
        except (NoForecastError, track_fit.UnknownCircuitError, form.NoPairingsError):
            continue
        codes = [code for code in predicted.outcome.driver_code if code in finished]
        if len(codes) < 2:
            continue
        starters = len(predicted.outcome.driver_code)
        seen = {code: slot for slot, code in enumerate(predicted.outcome.driver_code)}
        keep = [seen[code] for code in codes]
        grids[MODEL].append(_renormalised(predicted.outcome.probabilities()[keep], starters))

        field = pane.entries.get((context.season, context.round))
        if field is None:
            grids[MODEL].pop()
            continue
        fitted = baselines.fit(entries, context.race_date, known=pane.entries)
        guessed = fitted.predict(field)
        order = {code: slot for slot, code in enumerate(field.driver_code)}
        rows = [order[code] for code in codes if code in order]
        if len(rows) != len(codes):
            grids[MODEL].pop()
            continue
        for name in baselines.FEATURES:
            grids[name].append(_renormalised(guessed[name][rows], starters))

        actual = np.array([finished[code] for code in codes]) - 1
        truth.append(actual)
        labels.append(np.full(len(codes), index))
        per_race.append(
            RaceScore(
                season=context.season,
                round=context.round,
                circuit_id=context.circuit_id,
                race_date=context.race_date,
                starters=starters,
                log_loss={name: log_loss(grids[name][-1], actual) for name in grids},
            )
        )
        if not assumptions:
            assumptions = predicted.assumptions

    if not truth:
        raise NoForecastError("no race in the holdout produced both a forecast and a result")
    outcome = np.concatenate(truth)
    race_of = np.concatenate(labels)
    scored = [
        _score(name, np.vstack(grids[name]), outcome, race_of, rng)
        for name in (MODEL, *baselines.FEATURES)
    ]
    ours = next(item for item in scored if item.name == MODEL)
    beats = all(ours.log_loss.value < item.log_loss.value for item in scored if item.name != MODEL)
    mine = np.vstack(grids[MODEL])
    paired = [
        _paired(name, mine, np.vstack(grids[name]), outcome, race_of, rng)
        for name in baselines.FEATURES
    ]
    return Report(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        from_season=from_season,
        holdout=holdout,
        paths=paths,
        field=FIELD,
        seed=seed,
        scored=scored,
        paired=paired,
        per_race=per_race,
        beats_baselines=beats,
        assumptions=assumptions,
    )


def _finishers(pane: Panel, context: EventContext) -> dict[str, int]:
    rows = pane.results.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).drop_nulls(["driver_code", "position"])
    return {str(row["driver_code"]): int(row["position"]) for row in rows.iter_rows(named=True)}


def _paired(
    baseline: str,
    mine: np.ndarray,
    theirs: np.ndarray,
    outcome: np.ndarray,
    race_of: np.ndarray,
    rng: np.random.Generator,
) -> Paired:
    return Paired(
        baseline=baseline,
        log_loss_gain=race_bootstrap(
            race_of,
            lambda rows: (
                log_loss(theirs[rows], outcome[rows]) - log_loss(mine[rows], outcome[rows])
            ),
            rng,
        ),
        brier_gain=race_bootstrap(
            race_of,
            lambda rows: brier(theirs[rows], outcome[rows]) - brier(mine[rows], outcome[rows]),
            rng,
        ),
    )


def _score(
    name: str,
    grid: np.ndarray,
    outcome: np.ndarray,
    race_of: np.ndarray,
    rng: np.random.Generator,
) -> Scored:
    curves: dict[str, list[Bin]] = {}
    calibration: dict[str, float] = {}
    for event in EVENTS:
        cut = EVENT_CUTOFF[event]
        probability = grid[:, :cut].sum(axis=1)
        happened = (outcome < cut).astype(float)
        bins = curve(probability, happened)
        curves[event] = bins
        calibration[event] = float(
            sum(item.count * abs(item.forecast - item.observed) for item in bins)
            / max(sum(item.count for item in bins), 1)
        )
    return Scored(
        name=name,
        rows=int(grid.shape[0]),
        races=int(np.unique(race_of).shape[0]),
        log_loss=race_bootstrap(race_of, lambda rows: log_loss(grid[rows], outcome[rows]), rng),
        brier=race_bootstrap(race_of, lambda rows: brier(grid[rows], outcome[rows]), rng),
        calibration=calibration,
        curves=curves,
    )
