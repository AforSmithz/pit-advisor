# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from pydantic import BaseModel

HALF_LIFE_EVENTS = 30.0
# a circuit has at most a handful of races in the lake, so the rate is a shrunk one. the
# pseudo-exposure is roughly two races' worth of laps
PRIOR_LAPS = 120.0
MIN_PERIOD_LAPS = 2.0
COLUMNS = ("season", "round", "race_date", "circuit_id", "lap", "safety_car")


class SafetyCarModel(BaseModel, frozen=True):
    as_of: date
    circuit_id: str
    half_life_events: float
    # probability that a new period is called on a lap that is currently running green
    per_lap: float
    field_per_lap: float
    mean_period_laps: float
    weighted_races: float
    events_used: int


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _periods(flag: np.ndarray) -> tuple[int, float]:
    """A run of flagged laps is one deployment, however long it lasted."""
    if not flag.size:
        return 0, 0.0
    starts = int((flag[:1].astype(int).sum()) + int((flag[1:] & ~flag[:-1]).sum()))
    return starts, float(flag.sum())


def fit(
    frame: pl.DataFrame, circuit_id: str, as_of: date, half_life: float = HALF_LIFE_EVENTS
) -> SafetyCarModel:
    history = frame.filter(pl.col("race_date") < as_of)
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on=["season", "round", "race_date"])

    green_laps = {"field": 0.0, "circuit": 0.0}
    deployments = {"field": 0.0, "circuit": 0.0}
    flagged = {"field": 0.0, "circuit": 0.0}
    races = 0.0
    for (season, round_, circuit, ago), race in joined.group_by(
        "season", "round", "circuit_id", "events_ago"
    ):
        del season, round_
        weight = float(decay(np.asarray([float(str(ago))]), half_life)[0])
        flag = race.sort("lap")["safety_car"].to_numpy().astype(bool)
        starts, laps_under = _periods(flag)
        for scope in ("field", "circuit"):
            if scope == "circuit" and str(circuit) != circuit_id:
                continue
            # a lap already under a safety car cannot start a new one, so it is not exposure
            green_laps[scope] += weight * float(flag.size - laps_under)
            deployments[scope] += weight * starts
            flagged[scope] += weight * laps_under
            if scope == "circuit":
                races += weight

    field_rate = deployments["field"] / max(green_laps["field"], 1.0)
    prior = field_rate * PRIOR_LAPS
    per_lap = (deployments["circuit"] + prior) / (green_laps["circuit"] + PRIOR_LAPS)
    total_periods = deployments["field"] or 1.0
    mean_laps = max(flagged["field"] / total_periods, MIN_PERIOD_LAPS)
    return SafetyCarModel(
        as_of=as_of,
        circuit_id=circuit_id,
        half_life_events=half_life,
        per_lap=float(per_lap),
        field_per_lap=float(field_rate),
        mean_period_laps=float(mean_laps),
        weighted_races=float(races),
        events_used=events.height,
    )


def sample(
    model: SafetyCarModel, laps: int, paths: int, rng: np.random.Generator
) -> npt.NDArray[np.bool_]:
    """(paths, laps) of whether the field is behind a safety car on that lap."""
    active = np.zeros((paths, laps), dtype=bool)
    remaining = np.zeros(paths, dtype=int)
    for lap in range(laps):
        running = remaining > 0
        called = (rng.random(paths) < model.per_lap) & ~running
        # a geometric duration is the memoryless one, which is what a per-lap clear-up is
        drawn = rng.geometric(1.0 / model.mean_period_laps, size=paths)
        remaining = np.where(called, drawn, remaining)
        active[:, lap] = remaining > 0
        remaining = np.maximum(remaining - 1, 0)
    return active
