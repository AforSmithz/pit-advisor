# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from pydantic import BaseModel

# the front row can only lose places and the back row can only gain them, so one pooled
# distribution would put mass where the geometry forbids it
BUCKETS = ((1, 3), (4, 8), (9, 14), (15, 20))
MAX_GAIN = 10
# starts change with the regulations, not with the week, so the window is most of the lake
HALF_LIFE_EVENTS = 40.0
PRIOR = 1.0
COLUMNS = ("season", "round", "race_date", "grid", "start_position")


class NoStartsError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class Bucket(BaseModel, frozen=True):
    low: int
    high: int
    # places gained on lap one, indexed from -MAX_GAIN, so slot MAX_GAIN is holding station
    gain: list[float]
    samples: float

    @property
    def mean(self) -> float:
        weight = np.asarray(self.gain)
        return float((np.arange(-MAX_GAIN, MAX_GAIN + 1) * weight).sum())


class StartModel(BaseModel, frozen=True):
    as_of: date
    half_life_events: float
    buckets: list[Bucket]
    events_used: int
    events_dropped: int


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _bucket_of(grid: np.ndarray) -> np.ndarray:
    index = np.zeros(grid.shape, dtype=int)
    for slot, (_, high) in enumerate(BUCKETS):
        index = np.where(grid > high, slot + 1, index)
    return np.clip(index, 0, len(BUCKETS) - 1)


def fit(frame: pl.DataFrame, as_of: date, half_life: float = HALF_LIFE_EVENTS) -> StartModel:
    history = frame.filter(pl.col("race_date") < as_of).drop_nulls(["grid", "start_position"])
    dropped = frame.height - history.height
    if not history.height:
        raise NoStartsError(f"no lap-one positions before {as_of}")
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on=["season", "round", "race_date"])
    weight = decay(joined["events_ago"].to_numpy().astype(float), half_life)
    grid = joined["grid"].to_numpy().astype(int)
    gained = np.clip(grid - joined["start_position"].to_numpy().astype(int), -MAX_GAIN, MAX_GAIN)
    slot = _bucket_of(grid)

    buckets: list[Bucket] = []
    for index, (low, high) in enumerate(BUCKETS):
        inside = slot == index
        counts = np.full(2 * MAX_GAIN + 1, PRIOR / (2 * MAX_GAIN + 1))
        np.add.at(counts, gained[inside] + MAX_GAIN, weight[inside])
        buckets.append(
            Bucket(
                low=low,
                high=high,
                gain=[float(value) for value in counts / counts.sum()],
                samples=float(weight[inside].sum()),
            )
        )
    return StartModel(
        as_of=as_of,
        half_life_events=half_life,
        buckets=buckets,
        events_used=events.height,
        events_dropped=dropped,
    )


def sample(
    model: StartModel, grid: np.ndarray, rng: np.random.Generator, paths: int
) -> npt.NDArray[np.int64]:
    """Independent draws are not a permutation, so the gains are an intent and the running
    order is what the field settles into once every intent is resolved against the others."""
    slots = np.asarray(grid, dtype=int)
    index = _bucket_of(slots)
    table = np.asarray([bucket.gain for bucket in model.buckets])
    intent = np.empty((paths, slots.shape[0]))
    values = np.arange(-MAX_GAIN, MAX_GAIN + 1)
    for driver, bucket in enumerate(index):
        drawn = rng.choice(values, size=paths, p=table[bucket])
        intent[:, driver] = slots[driver] - drawn
    # a tie between two cars that both want the same place is broken at random, not by name
    order = np.argsort(intent + rng.uniform(0.0, 0.5, intent.shape), axis=1, kind="stable")
    # argsort of an argsort is the rank, which is the running order read per driver
    return np.asarray(np.argsort(order, axis=1) + 1, dtype=np.int64)
