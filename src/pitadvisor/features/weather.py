# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date, datetime, timedelta
from enum import StrEnum

import numpy as np
import polars as pl
from pydantic import BaseModel

# below this an intermediate is slower than a slick, so the lap is still a dry lap
WET_MM_PER_HOUR = 0.5
RACE_WINDOW = timedelta(hours=3)
HALF_LIFE_EVENTS = 20.0
# the prior spread of wet-weather ability, in percentage points of race pace. one
# percentage point is about a second a lap, which is the whole plausible range: nobody is
# four seconds a lap better in the rain than his own dry pace says
PRIOR_SD = 1.0
CONFIDENCE_Z = 1.96

COLUMNS = (
    "season",
    "round",
    "race_date",
    "driver_code",
    "constructor_id",
    "is_wet",
    "value",
)


class Scenario(StrEnum):
    DRY = "dry"
    MIXED = "mixed"
    WET = "wet"


class NoForecastError(RuntimeError):
    def __init__(self, circuit_id: str) -> None:
        super().__init__(f"no weather rows cover the session window at {circuit_id}")


class ScenarioWeights(BaseModel, frozen=True):
    dry: float
    mixed: float
    wet: float
    hours: int
    is_forecast: bool
    # the newest ingest that fed this, so a past call can be replayed against what was known
    snapshot_at: datetime
    expected_mm: float
    wettest_hour: float
    driest_hour: float

    def as_dict(self) -> dict[Scenario, float]:
        return {Scenario.DRY: self.dry, Scenario.MIXED: self.mixed, Scenario.WET: self.wet}


def hourly_wet(probability: float | None, millimetres: float) -> float:
    """Open-Meteo's precipitation is already an expectation over the hour, so dividing it by
    the chance of rain recovers how hard it would come down if it did rain. Drizzle that
    never reaches the intermediate crossover is a dry hour however likely it is."""
    if probability is None:
        return 1.0 if millimetres >= WET_MM_PER_HOUR else 0.0
    chance = probability / 100.0
    if chance <= 0.0:
        return 0.0
    return chance if millimetres / chance >= WET_MM_PER_HOUR else 0.0


def scenario(
    rows: pl.DataFrame,
    start: datetime,
    window: timedelta = RACE_WINDOW,
    as_of: datetime | None = None,
) -> ScenarioWeights:
    covering = rows.filter(
        (pl.col("observed_at") >= start) & (pl.col("observed_at") < start + window)
    )
    if as_of is not None:
        covering = covering.filter(pl.col("ingested_at") <= as_of)
    if not covering.height:
        raise NoForecastError(str(rows["circuit_id"][0]) if rows.height else "?")
    # one circuit gets re-forecast every run, so keep the newest row per hour and no more
    latest = covering.sort("ingested_at").group_by("observed_at").last().sort("observed_at")

    wet = np.array(
        [
            hourly_wet(row["precipitation_probability"], float(row["precipitation_mm"]))
            for row in latest.iter_rows(named=True)
        ]
    )
    # rain persists across hours far more than it flips, so the hours are coupled at the
    # extreme rather than treated as independent. independence would invent a mixed race
    # out of two hours that are really the same weather system
    wettest, driest = float(wet.max()), float(wet.min())
    return ScenarioWeights(
        dry=1.0 - wettest,
        mixed=wettest - driest,
        wet=driest,
        hours=latest.height,
        is_forecast=bool(latest["is_forecast"].any()),
        snapshot_at=latest["ingested_at"].max(),  # pyright: ignore[reportArgumentType]
        expected_mm=float(latest["precipitation_mm"].sum()),
        wettest_hour=wettest,
        driest_hour=driest,
    )


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


class WetDelta(BaseModel, frozen=True):
    key: str
    # positive is worse in the rain: the value being differenced is percent off the benchmark
    delta: float
    # the posterior mean under the prior above, which is what a consumer should use. a
    # driver with one wet race lands near zero with an interval the width of the prior
    shrunk_delta: float
    standard_error: float
    interval_low: float
    interval_high: float
    wet_sessions: int
    dry_sessions: int
    effective_wet: float


class WetForm(BaseModel, frozen=True):
    as_of: date
    half_life_events: float
    prior_sd: float
    drivers: list[WetDelta]
    teams: list[WetDelta]
    # a season can pass without a single wet race, so this is the number that says whether
    # any of the deltas below mean anything at all
    wet_sessions: int
    events_used: int
    events_dropped: int


def _sided(frame: pl.DataFrame, key: str) -> dict[str, tuple[float, float, int, int, float]]:
    found: dict[str, tuple[float, float, int, int, float]] = {}
    for name, rows in frame.group_by(key):
        wet = rows.filter(pl.col("is_wet"))
        dry = rows.filter(~pl.col("is_wet"))
        if not wet.height or not dry.height:
            continue
        wet_weight = wet["weight"].to_numpy()
        dry_weight = dry["weight"].to_numpy()
        wet_mean = float(wet_weight @ wet["value"].to_numpy() / wet_weight.sum())
        dry_mean = float(dry_weight @ dry["value"].to_numpy() / dry_weight.sum())
        spread = _spread(wet, wet_weight, wet_mean) + _spread(dry, dry_weight, dry_mean)
        found[str(name[0])] = (
            wet_mean - dry_mean,
            float(np.sqrt(spread)),
            wet.height,
            dry.height,
            float(wet_weight.sum()),
        )
    return found


def _spread(rows: pl.DataFrame, weight: np.ndarray, mean: float) -> float:
    if rows.height < 2:
        return float("inf")
    total = float(weight.sum())
    squared = float((weight**2).sum())
    variance = float(weight @ (rows["value"].to_numpy() - mean) ** 2) / max(
        total - squared / total, 1e-9
    )
    return max(variance, 0.0) * squared / total**2


def _posterior(delta: float, error: float, prior_sd: float) -> tuple[float, float]:
    if not np.isfinite(error):
        return 0.0, prior_sd
    shrink = prior_sd**2 / (prior_sd**2 + error**2)
    return shrink * delta, float(np.sqrt(shrink) * error)


def _deltas(frame: pl.DataFrame, key: str, prior_sd: float) -> list[WetDelta]:
    found: list[WetDelta] = []
    for name, (delta, error, wet, dry, effective) in sorted(_sided(frame, key).items()):
        shrunk, posterior = _posterior(delta, error, prior_sd)
        found.append(
            WetDelta(
                key=name,
                delta=delta,
                shrunk_delta=shrunk,
                standard_error=posterior,
                interval_low=shrunk - CONFIDENCE_Z * posterior,
                interval_high=shrunk + CONFIDENCE_Z * posterior,
                wet_sessions=wet,
                dry_sessions=dry,
                effective_wet=effective,
            )
        )
    return found


def wet_form(
    frame: pl.DataFrame,
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
    prior_sd: float = PRIOR_SD,
) -> WetForm:
    history = frame.filter(pl.col("race_date") < as_of)
    events = history.select("race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on="race_date")
    weighted = joined.with_columns(
        pl.Series("weight", decay(joined["events_ago"].to_numpy().astype(float), half_life))
    )
    return WetForm(
        as_of=as_of,
        half_life_events=half_life,
        prior_sd=prior_sd,
        drivers=_deltas(weighted, "driver_code", prior_sd),
        teams=_deltas(weighted, "constructor_id", prior_sd),
        wet_sessions=weighted.filter(pl.col("is_wet")).select("season", "round").unique().height,
        events_used=events.height,
        events_dropped=frame.height - history.height,
    )


def adjusted(pace: float, delta: float, weights: ScenarioWeights) -> float:
    """A scenario-weighted pace input for the simulation. Mixed gets half the wet penalty
    because half the race is run on the wrong tyre for the track, not on a wet track."""
    return pace + delta * (weights.wet + 0.5 * weights.mixed)
