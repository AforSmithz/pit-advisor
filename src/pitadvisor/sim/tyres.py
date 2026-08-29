# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from pydantic import BaseModel

HALF_LIFE_EVENTS = 20.0
MAX_STOPS = 3
# a circuit brings a handful of races, so every number here is shrunk toward the field with
# a pseudo-count worth about two race weekends of evidence
PRIOR_EVENTS = 2.0
PRIOR_STOPS = 20.0
# the window a stop can fall in. outside it a car is either serving a penalty or reacting to
# a safety car, and neither is a tyre decision
MIN_FRACTION = 0.08
MAX_FRACTION = 0.92
COLUMNS = (
    "season",
    "round",
    "race_date",
    "circuit_id",
    "driver_code",
    "stop",
    "fraction",
    "excess_millis",
    "starters",
)
DEGRADATION_COLUMNS = ("season", "round", "race_date", "circuit_id", "millis_per_lap")


class TyreModel(BaseModel, frozen=True):
    as_of: date
    circuit_id: str
    half_life_events: float
    # what one more lap on the same set costs, in milliseconds
    degradation_millis: float
    field_degradation_millis: float
    # in-lap plus out-lap, measured against the same driver's own green pace
    pit_loss_millis: float
    field_pit_loss_millis: float
    # under a safety car the field is already crawling, so a stop costs a fraction of that
    safety_car_discount: float
    stop_counts: list[float]
    stop_spread: float
    events_used: int
    weighted_events: float


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _weighted(frame: pl.DataFrame, half_life: float) -> tuple[pl.DataFrame, np.ndarray]:
    events = frame.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = frame.join(ranked, on=["season", "round", "race_date"])
    return joined, decay(joined["events_ago"].to_numpy().astype(float), half_life)


def _median(value: np.ndarray, weight: np.ndarray) -> float:
    """A pit stop under a red flag lands in the same column as one under green and is four
    times the number, so the middle of the distribution is the only statistic that survives."""
    if not value.size:
        return 0.0
    order = np.argsort(value)
    ranked: npt.NDArray[np.float64] = np.asarray(value, dtype=np.float64)[order]
    cumulative: npt.NDArray[np.float64] = np.cumsum(np.asarray(weight, dtype=np.float64)[order])
    total = float(cumulative[-1])
    if total <= 0.0:
        return float(np.median(ranked))
    return float(ranked[int(np.searchsorted(cumulative, total / 2.0))])


def _shrunk(here: np.ndarray, weight: np.ndarray, field: float, prior: float) -> float:
    if not here.size:
        return field
    mass = float(weight.sum())
    return float((_median(here, weight) * mass + field * prior) / (mass + prior))


SAFETY_CAR_DISCOUNT = 0.45


def fit(
    stops: pl.DataFrame,
    degradation: pl.DataFrame,
    circuit_id: str,
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
) -> TyreModel:
    deg_history = degradation.filter(pl.col("race_date") < as_of).drop_nulls("millis_per_lap")
    field_deg, here_deg = 0.0, 0.0
    if deg_history.height:
        joined, weight = _weighted(deg_history, half_life)
        value = joined["millis_per_lap"].to_numpy().astype(float)
        here = (joined["circuit_id"] == circuit_id).to_numpy()
        field_deg = _median(value, weight)
        here_deg = _shrunk(value[here], weight[here], field_deg, PRIOR_EVENTS)

    history = stops.filter(pl.col("race_date") < as_of)
    field_loss, here_loss, counts, spread, weighted, events = (
        0.0,
        0.0,
        [0.0, 1.0, 0.0, 0.0],
        0.08,
        0.0,
        0,
    )
    if history.height:
        joined, weight = _weighted(history, half_life)
        here = (joined["circuit_id"] == circuit_id).to_numpy()
        loss = joined["excess_millis"].to_numpy().astype(float)
        known = np.isfinite(loss)
        field_loss = _median(loss[known], weight[known])
        both = here & known
        here_loss = _shrunk(loss[both], weight[both], field_loss, PRIOR_EVENTS)
        counts = _stop_counts(joined, weight, here)
        spread = _spread(joined, weight, here)
        weighted = float(weight[here].sum())
        events = history.select("season", "round").unique().height
    return TyreModel(
        as_of=as_of,
        circuit_id=circuit_id,
        half_life_events=half_life,
        degradation_millis=here_deg,
        field_degradation_millis=field_deg,
        pit_loss_millis=here_loss,
        field_pit_loss_millis=field_loss,
        safety_car_discount=SAFETY_CAR_DISCOUNT,
        stop_counts=counts,
        stop_spread=spread,
        events_used=events,
        weighted_events=weighted,
    )


def _stop_counts(joined: pl.DataFrame, weight: np.ndarray, here: np.ndarray) -> list[float]:
    """A car that never pitted leaves no row, so zero-stoppers are counted as the starters
    the stop rows do not account for."""
    counts = np.zeros(MAX_STOPS + 1)
    framed = joined.with_columns(pl.Series("weight", weight), pl.Series("here", here))
    scoped = framed.filter(pl.col("here"))
    if not scoped.height:
        return [0.0, 1.0, 0.0, 0.0]
    per_driver = scoped.group_by("season", "round", "driver_code").agg(
        pl.col("stop").max().alias("stops"), pl.col("weight").first().alias("weight")
    )
    for row in per_driver.iter_rows(named=True):
        counts[min(int(row["stops"]), MAX_STOPS)] += float(row["weight"])
    per_race = scoped.group_by("season", "round").agg(
        pl.col("starters").first().alias("starters"),
        pl.col("driver_code").n_unique().alias("stopped"),
        pl.col("weight").first().alias("weight"),
    )
    for row in per_race.iter_rows(named=True):
        counts[0] += float(row["weight"]) * max(int(row["starters"]) - int(row["stopped"]), 0)
    smoothed = counts + PRIOR_STOPS * np.array([0.02, 0.5, 0.42, 0.06])
    return [float(value) for value in smoothed / smoothed.sum()]


def _spread(joined: pl.DataFrame, weight: np.ndarray, here: np.ndarray) -> float:
    """How far a stop drifts from the lap an even split would put it on."""
    framed = joined.with_columns(pl.Series("weight", weight), pl.Series("here", here)).filter(
        pl.col("here")
    )
    if not framed.height:
        return 0.08
    per_driver = framed.group_by("season", "round", "driver_code").agg(
        pl.col("stop").max().alias("stops")
    )
    with_total = framed.join(per_driver, on=["season", "round", "driver_code"])
    fraction = with_total["fraction"].to_numpy().astype(float)
    even = with_total["stop"].to_numpy().astype(float) / (
        with_total["stops"].to_numpy().astype(float) + 1.0
    )
    mass = with_total["weight"].to_numpy().astype(float)
    variance = float((mass * (fraction - even) ** 2).sum() / max(mass.sum(), 1e-9))
    return float(max(np.sqrt(variance), 0.02))


def sample_stops(
    model: TyreModel, paths: int, drivers: int, laps: int, rng: np.random.Generator
) -> npt.NDArray[np.bool_]:
    """(paths, drivers, laps) of whether that car comes in at the end of that lap."""
    pitting = np.zeros((paths, drivers, laps), dtype=bool)
    counts = np.asarray(model.stop_counts)
    planned = rng.choice(len(counts), size=(paths, drivers), p=counts / counts.sum())
    for stop in range(1, MAX_STOPS + 1):
        doing = planned >= stop
        if not doing.any():
            continue
        even = stop / (planned + 1.0)
        fraction = np.clip(
            even + rng.normal(0.0, model.stop_spread, (paths, drivers)),
            MIN_FRACTION,
            MAX_FRACTION,
        )
        lap = np.clip(np.rint(fraction * laps).astype(int), 1, laps - 1)
        rows, cars = np.nonzero(doing)
        pitting[rows, cars, lap[rows, cars]] = True
    return pitting
