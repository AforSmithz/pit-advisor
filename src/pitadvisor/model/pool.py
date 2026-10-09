from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from pitadvisor.model.metrics import log_loss

SIM_POWERS = tuple(round(value, 2) for value in np.linspace(0.6, 2.0, 15))
GRID_POWERS = tuple(round(value, 2) for value in np.linspace(0.0, 1.0, 11))
FLOOR = 1e-9
SWEEPS = 200
TOLERANCE = 1e-9


class Pool(BaseModel, frozen=True):
    """p is proportional to sim^a x grid^b per driver, then balanced so every place is taken
    once. a above one says the simulation spreads the field wider than races do."""

    sim_power: float
    grid_power: float
    races: int


IDENTITY = Pool(sim_power=1.0, grid_power=0.0, races=0)


@dataclass(frozen=True)
class Scored:
    sim: npt.NDArray[np.float64]
    grid: npt.NDArray[np.float64]
    actual: npt.NDArray[np.int64]
    starters: int


def balance(grid: npt.NDArray[np.float64], places: int) -> npt.NDArray[np.float64]:
    """Rows are drivers and sum to one. A column is one place, and between them the drivers
    hold exactly their share of it: all of it when every starter is a row, less when the
    rows are only the classified cars."""
    if not grid.size:
        return grid
    held = np.full(places, grid.shape[0] / places)
    out = np.array(grid, dtype=np.float64)
    for _ in range(SWEEPS):
        out *= (held / np.maximum(out.sum(axis=0), FLOOR)).reshape(1, -1)
        out /= out.sum(axis=1, keepdims=True)
        if np.abs(out.sum(axis=0) - held).max() < TOLERANCE:
            break
    return out


def combine(
    sim: npt.NDArray[np.float64],
    grid: npt.NDArray[np.float64] | None,
    starters: int,
    pool: Pool,
) -> npt.NDArray[np.float64]:
    places = min(starters, sim.shape[1])
    logged = pool.sim_power * np.log(np.maximum(sim[:, :places], FLOOR))
    if grid is not None and pool.grid_power:
        logged = logged + pool.grid_power * np.log(np.maximum(grid[:, :places], FLOOR))
    raw = np.exp(logged - logged.max(axis=1, keepdims=True))
    raw /= raw.sum(axis=1, keepdims=True)
    out = np.zeros(sim.shape)
    out[:, :places] = balance(raw, places)
    return out


def fit(history: Sequence[Scored], with_grid: bool = True) -> Pool:
    """Grid search over both powers on races already run, scored race by race. Nothing in
    here sees the race the pool is about to be applied to: the caller decides what history is."""
    if not history:
        return IDENTITY
    grid_powers = GRID_POWERS if with_grid else (0.0,)
    best: tuple[float, float, float] | None = None
    for a in SIM_POWERS:
        for b in grid_powers:
            pool = Pool(sim_power=a, grid_power=b, races=len(history))
            loss = float(
                np.mean(
                    [
                        log_loss(combine(race.sim, race.grid, race.starters, pool), race.actual)
                        for race in history
                    ]
                )
            )
            if best is None or loss < best[0]:
                best = (loss, a, b)
    assert best is not None
    return Pool(sim_power=best[1], grid_power=best[2], races=len(history))
