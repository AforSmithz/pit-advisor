from datetime import date

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from pitadvisor.sim import starts, tyres
from pitadvisor.sim.dnf import RetirementModel
from pitadvisor.sim.dnf import sample as sample_retirements
from pitadvisor.sim.overtake import STRIKING_MILLIS, PassModel, probability
from pitadvisor.sim.safety_car import SafetyCarModel
from pitadvisor.sim.safety_car import sample as sample_safety_car
from pitadvisor.sim.starts import StartModel
from pitadvisor.sim.tyres import TyreModel

FIELD = 20
PATHS = 4000
# a car that fails to pass settles about this far back, which is where the dirty air starts
FOLLOWING_GAP_MILLIS = 700.0
# what a safety car compresses the field to before it peels in
BUNCHING_GAP_MILLIS = 1_200.0
SAFETY_CAR_LAP_FACTOR = 1.4
# lap to lap scatter around a driver's own race pace, over and above the rating's own error
LAP_NOISE_MILLIS = 300.0
# retired cars are held out of the running order rather than deleted, so the arrays keep
# their shape. one lap of separation is more than any real gap
OUT_OF_RACE_MILLIS = 1e9


class Driver(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    grid: int
    pace_millis: float
    # everything we do not know about Sunday: the rating's own error and the driver's
    # week to week scatter around it
    pace_sd_millis: float


class RaceSetup(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    race_date: date
    laps: int
    reference_millis: float
    drivers: list[Driver]
    start: StartModel
    tyre: TyreModel
    passing: PassModel
    safety_car: SafetyCarModel
    retirement: RetirementModel


class Outcome(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    scenario: str
    paths: int
    driver_code: list[str]
    # (drivers, FIELD) probability of each classified finishing position
    position: list[list[float]]
    win: list[float]
    podium: list[float]
    points: list[float]
    finish: list[float]
    expected_position: list[float]

    def probabilities(self) -> npt.NDArray[np.float64]:
        return np.asarray(self.position, dtype=np.float64)


def simulate(
    setup: RaceSetup,
    rng: np.random.Generator,
    paths: int = PATHS,
    scenario: str = "dry",
) -> Outcome:
    codes = [driver.driver_code for driver in setup.drivers]
    cars, laps = len(codes), setup.laps
    rows: npt.NDArray[np.int64] = np.arange(paths, dtype=np.int64)

    mean = np.asarray([driver.pace_millis for driver in setup.drivers])
    spread = np.asarray([driver.pace_sd_millis for driver in setup.drivers])
    # one draw per path, not per lap: a driver who has a quick Sunday has it all afternoon
    pace = rng.normal(mean.reshape(1, -1), np.maximum(spread, 1.0).reshape(1, -1), (paths, cars))

    grid = np.asarray([driver.grid for driver in setup.drivers])
    opening = starts.sample(setup.start, grid, rng, paths)
    retire = sample_retirements(setup.retirement, codes, laps, paths, rng)
    pitting = tyres.sample_stops(setup.tyre, paths, cars, laps, rng)
    caution = sample_safety_car(setup.safety_car, laps, paths, rng)

    # the grid itself is track position, so the race starts from the order lap one produced
    elapsed = opening.astype(float) * FOLLOWING_GAP_MILLIS
    age = np.ones((paths, cars))
    out = retire <= 1
    order = np.argsort(elapsed + out * OUT_OF_RACE_MILLIS, axis=1, kind="stable")

    for lap in range(1, laps + 1):
        running = retire > lap
        held = _in_traffic(elapsed, order, out)
        time = (
            pace
            + setup.tyre.degradation_millis * age
            + rng.normal(0.0, LAP_NOISE_MILLIS, (paths, cars))
        )
        under = caution[:, lap - 1].reshape(-1, 1)
        # dirty air is why the quick car behind stays behind: it does not get to spend the
        # advantage that closed the gap in the first place
        time = time + np.where(held & ~under, setup.passing.dirty_air_millis, 0.0)
        time = np.where(under, time * SAFETY_CAR_LAP_FACTOR, time)
        stopping = pitting[:, :, lap - 1]
        loss = setup.tyre.pit_loss_millis * np.where(under, setup.tyre.safety_car_discount, 1.0)
        time = time + np.where(stopping, loss, 0.0)
        age = np.where(stopping, 0.0, age) + 1.0
        elapsed = elapsed + np.where(running, time, 0.0)
        out = retire <= lap

        elapsed = _resolve(setup, elapsed, pace, order, out, rows, rng)
        if under.any():
            elapsed = _bunch(elapsed, order, out, under.ravel())
        order = np.argsort(elapsed + out * OUT_OF_RACE_MILLIS, axis=1, kind="stable")

    completed = np.minimum(retire - 1, laps)
    # a retirement is classified behind everyone who went further, and finishers are ordered
    # on time, which is exactly how the result sheet reads
    key = (laps - completed) * OUT_OF_RACE_MILLIS + elapsed
    place = np.argsort(np.argsort(key, axis=1, kind="stable"), axis=1) + 1
    return _aggregate(setup, codes, place, retire > laps, paths, scenario)


def _resolve(
    setup: RaceSetup,
    elapsed: npt.NDArray[np.float64],
    pace: npt.NDArray[np.float64],
    order: npt.NDArray[np.int64],
    out: npt.NDArray[np.bool_],
    rows: npt.NDArray[np.int64],
    rng: np.random.Generator,
) -> npt.NDArray[np.float64]:
    """Track position. A car only gets by if the pass comes off, and one that does not sits
    in the gearbox of the car ahead, which is what builds a train."""
    scale = 100.0 / max(setup.reference_millis, 1.0)
    paths = int(elapsed.shape[0])
    for slot in range(1, order.shape[1]):
        leader = order[:, slot - 1]
        chaser = order[:, slot]
        ahead = elapsed[rows, leader]
        behind = elapsed[rows, chaser]
        racing = ~out[rows, leader] & ~out[rows, chaser]
        attacking = racing & (behind < ahead)
        if not attacking.any():
            continue
        delta = (pace[rows, leader] - pace[rows, chaser]) * scale
        chance = probability(setup.passing, delta)
        blocked = attacking & (rng.random(paths) > chance)
        elapsed[rows, chaser] = np.where(blocked, ahead + FOLLOWING_GAP_MILLIS, behind)
    return elapsed


def _in_traffic(
    elapsed: npt.NDArray[np.float64],
    order: npt.NDArray[np.int64],
    out: npt.NDArray[np.bool_],
) -> npt.NDArray[np.bool_]:
    """Who is sitting inside the following gap of the car in front, read off the order."""
    running = np.take_along_axis(elapsed, order, axis=1)
    close = np.zeros(order.shape, dtype=bool)
    close[:, 1:] = np.diff(running, axis=1) < STRIKING_MILLIS
    rank = np.argsort(order, axis=1)
    return np.asarray(np.take_along_axis(close, rank, axis=1) & ~out, dtype=np.bool_)


def _bunch(
    elapsed: npt.NDArray[np.float64],
    order: npt.NDArray[np.int64],
    out: npt.NDArray[np.bool_],
    under: npt.NDArray[np.bool_],
) -> npt.NDArray[np.float64]:
    """A safety car deletes the gaps. Everything after it is a restart, not a continuation."""
    rank = np.argsort(order, axis=1)
    leader = np.where(out, np.inf, elapsed).min(axis=1, keepdims=True)
    closed = leader + rank * BUNCHING_GAP_MILLIS
    return np.where(under.reshape(-1, 1) & ~out, closed, elapsed)


def _aggregate(
    setup: RaceSetup,
    codes: list[str],
    place: npt.NDArray[np.int64],
    finished: npt.NDArray[np.bool_],
    paths: int,
    scenario: str,
) -> Outcome:
    grid = np.zeros((len(codes), FIELD))
    for driver in range(len(codes)):
        counts = np.bincount(np.clip(place[:, driver], 1, FIELD) - 1, minlength=FIELD)
        grid[driver] = counts / paths
    return Outcome(
        season=setup.season,
        round=setup.round,
        circuit_id=setup.circuit_id,
        scenario=scenario,
        paths=paths,
        driver_code=codes,
        position=[[float(value) for value in row] for row in grid],
        win=[float(row[0]) for row in grid],
        podium=[float(row[:3].sum()) for row in grid],
        points=[float(row[:10].sum()) for row in grid],
        finish=[float(value) for value in finished.mean(axis=0)],
        expected_position=[float((row * np.arange(1, FIELD + 1)).sum()) for row in grid],
    )


def blend(outcomes: dict[str, Outcome], weights: dict[str, float], scenario: str) -> Outcome:
    """One distribution over the weather, not one page per scenario. The weights are the
    forecast's, so a dry weekend leaves the wet paths carrying almost nothing."""
    names = [name for name in outcomes if weights.get(name, 0.0) > 0.0]
    if not names:
        raise ValueError("every scenario carries zero weight")
    total = sum(weights[name] for name in names)
    first = outcomes[names[0]]
    if any(outcomes[name].driver_code != first.driver_code for name in names):
        raise ValueError("scenarios were run over different fields")
    mixed = sum((weights[name] / total) * outcomes[name].probabilities() for name in names)
    grid = np.asarray(mixed)
    return Outcome(
        season=first.season,
        round=first.round,
        circuit_id=first.circuit_id,
        scenario=scenario,
        paths=sum(outcomes[name].paths for name in names),
        driver_code=first.driver_code,
        position=[[float(value) for value in row] for row in grid],
        win=[float(row[0]) for row in grid],
        podium=[float(row[:3].sum()) for row in grid],
        points=[float(row[:10].sum()) for row in grid],
        finish=[
            float(sum((weights[name] / total) * outcomes[name].finish[index] for name in names))
            for index in range(len(first.driver_code))
        ],
        expected_position=[float((row * np.arange(1, FIELD + 1)).sum()) for row in grid],
    )
