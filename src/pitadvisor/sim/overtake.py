# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from pydantic import BaseModel

from pitadvisor.features.clean_pace import TRAFFIC_THRESHOLD_MILLIS, is_green, with_gap_ahead

# a car more than this far back is not attacking, and counting its lap as a failed pass
# would put the base rate an order of magnitude below the one the simulation asks for
STRIKING_MILLIS = TRAFFIC_THRESHOLD_MILLIS
HALF_LIFE_EVENTS = 30.0
PRIOR_ATTEMPTS = 150.0
NEWTON_ROUNDS = 40
COLUMNS = (
    "season",
    "round",
    "race_date",
    "circuit_id",
    "lap",
    "ahead",
    "behind",
    "passed",
    "delta",
)
TRAFFIC_COLUMNS = ("season", "round", "race_date", "circuit_id", "driver_code", "penalty_millis")
PRIOR_TRAFFIC = 6.0
DEFAULT_DIRTY_AIR_MILLIS = 500.0


class PassModel(BaseModel, frozen=True):
    """logit(pass) = circuit base + slope x how much faster the trailing car is, in percent."""

    as_of: date
    circuit_id: str
    half_life_events: float
    base: float
    field_base: float
    slope: float
    # what a lap inside that range costs, measured against the same driver's own clean laps.
    # it is why a train forms: the chaser cannot use the advantage that put him there
    dirty_air_millis: float
    field_dirty_air_millis: float
    attempts: float
    slope_rows: int
    events_used: int


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _logit(p: float) -> float:
    bounded = min(max(p, 1e-6), 1.0 - 1e-6)
    return float(np.log(bounded / (1.0 - bounded)))


def pairs(laps: pl.DataFrame) -> pl.DataFrame:
    """Every lap on which one car sat inside striking range of the one ahead, and whether it
    was in front by the end of the next one. A pit lap on either side is not racing."""
    found: list[pl.DataFrame] = []
    for _, race in laps.group_by("season", "round"):
        racing = with_gap_ahead(race).filter(
            is_green(pl.col("track_status"))
            & pl.col("position").is_not_null()
            & ~pl.col("pit_in")
            & ~pl.col("pit_out")
        )
        chasing = racing.select(
            "season", "round", "lap", "position", "driver_code", "gap_ahead_millis"
        )
        leading = chasing.select(
            "season", "round", "lap", pl.col("position") + 1, pl.col("driver_code").alias("ahead")
        )
        # where each car ran a lap later, which is the only place a pass can show up
        later = racing.select(
            "season", "round", pl.col("lap") - 1, "driver_code", pl.col("position").alias("then")
        )
        found.append(
            chasing.join(leading, on=["season", "round", "lap", "position"], how="inner")
            .join(later, on=["season", "round", "lap", "driver_code"], how="inner")
            .rename({"then": "behind_then", "driver_code": "behind"})
            .join(
                later.rename({"driver_code": "ahead", "then": "ahead_then"}),
                on=["season", "round", "lap", "ahead"],
                how="inner",
            )
            .filter(
                pl.col("gap_ahead_millis").is_not_null()
                & (pl.col("gap_ahead_millis").abs() <= STRIKING_MILLIS)
            )
            .select(
                "season",
                "round",
                "lap",
                "ahead",
                "behind",
                (pl.col("behind_then") < pl.col("ahead_then")).alias("passed"),
            )
        )
    if not found:
        return pl.DataFrame(
            schema={
                "season": pl.Int64,
                "round": pl.Int64,
                "lap": pl.Int64,
                "ahead": pl.String,
                "behind": pl.String,
                "passed": pl.Boolean,
            }
        )
    return pl.concat(found)


def _slope(delta: np.ndarray, passed: np.ndarray, offset: np.ndarray, weight: np.ndarray) -> float:
    """One parameter logistic by Newton, with each row's circuit base rate as its offset."""
    beta = 0.0
    for _ in range(NEWTON_ROUNDS):
        p = 1.0 / (1.0 + np.exp(-(offset + beta * delta)))
        gradient = float((weight * delta * (passed - p)).sum())
        hessian = float((weight * delta * delta * p * (1.0 - p)).sum())
        if hessian <= 1e-12:
            break
        step = gradient / hessian
        beta += step
        if abs(step) < 1e-9:
            break
    return beta


def traffic_cost(
    frame: pl.DataFrame, circuit_id: str, as_of: date, half_life: float = HALF_LIFE_EVENTS
) -> tuple[float, float]:
    """A lap in dirty air against the same driver's own clean pace in the same session. The
    comparison is within a driver, so it is not the slow cars showing up as slow."""
    history = frame.filter(pl.col("race_date") < as_of).drop_nulls("penalty_millis")
    if not history.height:
        return DEFAULT_DIRTY_AIR_MILLIS, DEFAULT_DIRTY_AIR_MILLIS
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on=["season", "round", "race_date"])
    weight = decay(joined["events_ago"].to_numpy().astype(float), half_life)
    value = joined["penalty_millis"].to_numpy().astype(float)
    here = (joined["circuit_id"] == circuit_id).to_numpy()
    field = float((value * weight).sum() / max(weight.sum(), 1e-9))
    mass = float(weight[here].sum())
    if not mass:
        return field, field
    own = float((value[here] * weight[here]).sum() / mass)
    return (own * mass + field * PRIOR_TRAFFIC) / (mass + PRIOR_TRAFFIC), field


def fit(
    frame: pl.DataFrame,
    circuit_id: str,
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
    traffic: pl.DataFrame | None = None,
) -> PassModel:
    dirty, field_dirty = (
        traffic_cost(traffic, circuit_id, as_of, half_life)
        if traffic is not None
        else (DEFAULT_DIRTY_AIR_MILLIS, DEFAULT_DIRTY_AIR_MILLIS)
    )
    history = frame.filter(pl.col("race_date") < as_of)
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on=["season", "round", "race_date"])
    if not joined.height:
        return PassModel(
            as_of=as_of,
            circuit_id=circuit_id,
            half_life_events=half_life,
            base=_logit(0.1),
            field_base=_logit(0.1),
            slope=0.0,
            dirty_air_millis=dirty,
            field_dirty_air_millis=field_dirty,
            attempts=0.0,
            slope_rows=0,
            events_used=events.height,
        )
    weight = decay(joined["events_ago"].to_numpy().astype(float), half_life)
    passed = joined["passed"].to_numpy().astype(float)
    here = (joined["circuit_id"] == circuit_id).to_numpy()

    field_rate = float((weight * passed).sum() / max(weight.sum(), 1e-9))
    prior = field_rate * PRIOR_ATTEMPTS
    attempts = float(weight[here].sum())
    rate = (float((weight[here] * passed[here]).sum()) + prior) / (attempts + PRIOR_ATTEMPTS)

    # the slope is one number for the whole sport: a tenth of an advantage buys the same
    # extra chance everywhere, and only the circuit's base rate says how much a chance is
    scored = joined.drop_nulls("delta")
    slope, rows = 0.0, 0
    if scored.height:
        scored_weight = decay(scored["events_ago"].to_numpy().astype(float), half_life)
        offsets = np.where(
            (scored["circuit_id"] == circuit_id).to_numpy(), _logit(rate), _logit(field_rate)
        )
        slope = _slope(
            scored["delta"].to_numpy().astype(float),
            scored["passed"].to_numpy().astype(float),
            offsets,
            scored_weight,
        )
        rows = scored.height
    return PassModel(
        as_of=as_of,
        circuit_id=circuit_id,
        half_life_events=half_life,
        base=_logit(rate),
        field_base=_logit(field_rate),
        slope=float(slope),
        dirty_air_millis=dirty,
        field_dirty_air_millis=field_dirty,
        attempts=attempts,
        slope_rows=rows,
        events_used=events.height,
    )


def probability(model: PassModel, delta: np.ndarray) -> npt.NDArray[np.float64]:
    """delta is how much faster the chasing car is, in percent of a lap. Positive attacks."""
    scaled: npt.NDArray[np.float64] = np.asarray(delta, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(-(model.base + model.slope * scaled)))
