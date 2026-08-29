from collections.abc import Callable, Sequence

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

# a probability of exactly zero on the class that happened is an infinite loss, which says
# more about float underflow than about the forecast. the floor is well below any grid a
# 20 class problem produces, so it never binds on a sane prediction
FLOOR = 1e-15
SIMPLEX_TOLERANCE = 1e-6
BINS = 10
DRAWS = 2000
CREDIBLE = 0.95


class MalformedForecastError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


def _checked(probabilities: np.ndarray, actual: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    grid = np.asarray(probabilities, dtype=float)
    truth = np.asarray(actual, dtype=int)
    if grid.ndim != 2:
        raise MalformedForecastError(f"probabilities must be (rows, classes), got {grid.shape}")
    if truth.shape != (grid.shape[0],):
        raise MalformedForecastError(f"{truth.shape[0]} outcomes against {grid.shape[0]} rows")
    if grid.size and (grid < 0).any():
        raise MalformedForecastError("a negative probability is not a forecast")
    total = grid.sum(axis=1)
    if grid.size and np.abs(total - 1.0).max() > SIMPLEX_TOLERANCE:
        raise MalformedForecastError(f"rows sum to {total.min():.6f}..{total.max():.6f}, not 1")
    if grid.size and ((truth < 0) | (truth >= grid.shape[1])).any():
        raise MalformedForecastError("an outcome falls outside the class set")
    return grid, truth


def log_loss(probabilities: np.ndarray, actual: np.ndarray) -> float:
    grid, truth = _checked(probabilities, actual)
    if not grid.size:
        return float("nan")
    hit = grid[np.arange(grid.shape[0]), truth]
    return float(-np.log(np.maximum(hit, FLOOR)).mean())


def brier(probabilities: np.ndarray, actual: np.ndarray) -> float:
    """The multiclass form, summed over classes, so a K class score runs 0 to 2."""
    grid, truth = _checked(probabilities, actual)
    if not grid.size:
        return float("nan")
    onehot = np.zeros_like(grid)
    onehot[np.arange(grid.shape[0]), truth] = 1.0
    return float(((grid - onehot) ** 2).sum(axis=1).mean())


def binary_brier(probability: np.ndarray, outcome: np.ndarray) -> float:
    p = np.asarray(probability, dtype=float)
    y = np.asarray(outcome, dtype=float)
    if p.shape != y.shape:
        raise MalformedForecastError(f"{p.shape} probabilities against {y.shape} outcomes")
    if not p.size:
        return float("nan")
    return float(((p - y) ** 2).mean())


def accuracy(probabilities: np.ndarray, actual: np.ndarray) -> float:
    grid, truth = _checked(probabilities, actual)
    if not grid.size:
        return float("nan")
    return float((grid.argmax(axis=1) == truth).mean())


class Bin(BaseModel, frozen=True):
    low: float
    high: float
    forecast: float
    observed: float
    count: int


def reliability(probability: np.ndarray, outcome: np.ndarray, bins: int = BINS) -> list[Bin]:
    p = np.asarray(probability, dtype=float)
    y = np.asarray(outcome, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    # right-closed except at zero, so p=1 lands in the top bin rather than off the end
    index = np.clip(np.searchsorted(edges, p, side="left") - 1, 0, bins - 1)
    found: list[Bin] = []
    for slot in range(bins):
        inside = index == slot
        if not inside.any():
            continue
        found.append(
            Bin(
                low=float(edges[slot]),
                high=float(edges[slot + 1]),
                forecast=float(p[inside].mean()),
                observed=float(y[inside].mean()),
                count=int(inside.sum()),
            )
        )
    return found


def calibration_error(curve: Sequence[Bin]) -> float:
    """ECE: the count-weighted gap between what was promised and what happened."""
    total = sum(item.count for item in curve)
    if not total:
        return float("nan")
    return sum(item.count * abs(item.forecast - item.observed) for item in curve) / total


class Interval(BaseModel, frozen=True):
    value: float
    low: float
    high: float
    draws: int


def race_bootstrap(
    race: np.ndarray,
    statistic: Callable[[npt.NDArray[np.int64]], float],
    rng: np.random.Generator,
    draws: int = DRAWS,
    credible: float = CREDIBLE,
) -> Interval:
    """§4.4: drivers inside one race share a car, a strategy and a safety car, so resampling
    them independently reports an interval several times tighter than the evidence supports."""
    labels = np.asarray(race)
    rows: npt.NDArray[np.int64] = np.arange(int(labels.shape[0]), dtype=np.int64)
    point = statistic(rows)
    races, membership = np.unique(labels, return_inverse=True)
    if int(races.shape[0]) < 2:
        return Interval(value=point, low=float("nan"), high=float("nan"), draws=0)
    grouped: list[npt.NDArray[np.int64]] = [
        rows[membership == index] for index in range(int(races.shape[0]))
    ]
    sampled: list[float] = []
    for _ in range(draws):
        picked: list[int] = rng.integers(0, len(grouped), len(grouped)).tolist()
        value = statistic(np.concatenate([grouped[index] for index in picked]))
        if np.isfinite(value):
            sampled.append(value)
    if not sampled:
        return Interval(value=point, low=float("nan"), high=float("nan"), draws=0)
    tail = (1.0 - credible) / 2.0
    low, high = np.quantile(sampled, [tail, 1.0 - tail])
    return Interval(value=point, low=float(low), high=float(high), draws=len(sampled))
