from datetime import date

import numpy as np
import pytest

from pitadvisor.sim import race
from pitadvisor.sim.dnf import RetirementModel
from pitadvisor.sim.overtake import PassModel
from pitadvisor.sim.safety_car import SafetyCarModel
from pitadvisor.sim.starts import BUCKETS, MAX_GAIN, Bucket, StartModel
from pitadvisor.sim.tyres import TyreModel

AS_OF = date(2025, 1, 1)
REFERENCE = 90_000.0
CODES = [f"D{index:02d}" for index in range(10)]


def still() -> StartModel:
    holding = [0.0] * (2 * MAX_GAIN + 1)
    holding[MAX_GAIN] = 1.0
    return StartModel(
        as_of=AS_OF,
        half_life_events=40.0,
        buckets=[
            Bucket(low=low, high=high, gain=list(holding), samples=100.0) for low, high in BUCKETS
        ],
        events_used=20,
        events_dropped=0,
    )


def setup(
    pace: list[float] | None = None,
    hazard: float = 0.0,
    pass_base: float = -2.2,
    dirty_air: float = 400.0,
    safety_car: float = 0.0,
    spread: float = 1.0,
    laps: int = 40,
) -> race.RaceSetup:
    ratings = pace or [0.0] * len(CODES)
    return race.RaceSetup(
        season=2024,
        round=1,
        circuit_id="monza",
        race_date=AS_OF,
        laps=laps,
        reference_millis=REFERENCE,
        drivers=[
            race.Driver(
                driver_code=code,
                constructor_id="team",
                grid=index + 1,
                pace_millis=REFERENCE * (1.0 + ratings[index] / 100.0),
                pace_sd_millis=spread,
            )
            for index, code in enumerate(CODES)
        ],
        start=still(),
        tyre=TyreModel(
            as_of=AS_OF,
            circuit_id="monza",
            half_life_events=20.0,
            degradation_millis=50.0,
            field_degradation_millis=50.0,
            pit_loss_millis=22_000.0,
            field_pit_loss_millis=22_000.0,
            safety_car_discount=0.45,
            stop_counts=[0.0, 1.0, 0.0, 0.0],
            stop_spread=0.05,
            events_used=8,
            weighted_events=8.0,
        ),
        passing=PassModel(
            as_of=AS_OF,
            circuit_id="monza",
            half_life_events=30.0,
            base=pass_base,
            field_base=pass_base,
            slope=0.8,
            dirty_air_millis=dirty_air,
            field_dirty_air_millis=dirty_air,
            attempts=500.0,
            slope_rows=500,
            events_used=20,
        ),
        safety_car=SafetyCarModel(
            as_of=AS_OF,
            circuit_id="monza",
            half_life_events=30.0,
            per_lap=safety_car,
            field_per_lap=safety_car,
            mean_period_laps=4.0,
            weighted_races=8.0,
            events_used=20,
        ),
        retirement=RetirementModel(
            as_of=AS_OF,
            per_lap={code: hazard for code in CODES},
            field_per_lap=hazard,
            field_collision_per_lap=0.0,
            cause_coverage=0.5,
        ),
    )


def run(built: race.RaceSetup, paths: int = 600, seed: int = 3) -> race.Outcome:
    return race.simulate(built, np.random.default_rng(seed), paths=paths)


def test_every_driver_gets_a_whole_distribution():
    outcome = run(setup())
    grid = outcome.probabilities()
    assert grid.shape == (len(CODES), race.FIELD)
    assert np.allclose(grid.sum(axis=1), 1.0)
    # ten cars cannot finish eleventh
    assert not grid[:, len(CODES) :].any()


def test_exactly_one_car_wins_each_path():
    outcome = run(setup())
    assert sum(outcome.win) == pytest.approx(1.0)
    assert sum(outcome.podium) == pytest.approx(3.0)


def test_a_quicker_car_wins_more_often_than_a_slower_one():
    pace = [0.0] + [1.0] * (len(CODES) - 1)
    outcome = run(setup(pace=pace))
    assert outcome.win[0] > 0.8
    assert outcome.expected_position[0] < 2.0


def test_track_position_is_worth_something_when_nobody_can_pass():
    """Same car, same pace, everybody. If the running order still comes out flat the
    simulation is not modelling a race, it is shuffling a list."""
    even = run(setup(pass_base=-6.0, dirty_air=1_500.0))
    open_road = run(setup(pass_base=2.0, dirty_air=0.0))
    assert even.expected_position[0] < open_road.expected_position[0]
    assert even.expected_position[-1] > open_road.expected_position[-1]


def test_a_higher_hazard_finishes_fewer_cars():
    reliable = run(setup(hazard=0.0))
    fragile = run(setup(hazard=0.01))
    assert min(reliable.finish) == 1.0
    assert max(fragile.finish) < 0.75


def test_a_retirement_is_classified_behind_everyone_who_went_further():
    """A car that stops on lap two is last, however quick it was while it ran."""
    built = setup(hazard=0.0)
    stopping = built.model_copy(
        update={
            "retirement": built.retirement.model_copy(
                update={"per_lap": {**built.retirement.per_lap, CODES[0]: 0.9}}
            )
        }
    )
    outcome = race.simulate(stopping, np.random.default_rng(1), paths=400)
    assert outcome.finish[0] < 0.01
    assert outcome.expected_position[0] > len(CODES) - 1.5


def test_the_same_seed_gives_the_same_race():
    assert run(setup(), seed=8).position == run(setup(), seed=8).position


def test_a_safety_car_shuffles_the_order_more_than_a_clean_race():
    clean = run(setup(pass_base=-5.0, pace=[float(i) * 0.4 for i in range(len(CODES))]))
    chaotic = run(
        setup(
            pass_base=-5.0,
            safety_car=0.06,
            pace=[float(i) * 0.4 for i in range(len(CODES))],
        )
    )
    assert chaotic.win[0] < clean.win[0]


def test_blending_two_scenarios_weights_them():
    dry = run(setup(pace=[0.0] + [2.0] * (len(CODES) - 1)))
    wet = run(setup(pace=[2.0] * (len(CODES) - 1) + [0.0]))
    mixed = race.blend({"dry": dry, "wet": wet}, {"dry": 0.75, "wet": 0.25}, "blended")
    assert mixed.win[0] == pytest.approx(0.75 * dry.win[0] + 0.25 * wet.win[0])
    assert sum(mixed.win) == pytest.approx(1.0)
    assert mixed.scenario == "blended"


def test_blending_nothing_is_refused():
    dry = run(setup(), paths=50)
    with pytest.raises(ValueError, match="zero weight"):
        race.blend({"dry": dry}, {"dry": 0.0}, "blended")


def test_blending_two_different_fields_is_refused():
    dry = run(setup(), paths=50)
    other = dry.model_copy(update={"driver_code": list(reversed(CODES))})
    with pytest.raises(ValueError, match="different fields"):
        race.blend({"dry": dry, "wet": other}, {"dry": 0.5, "wet": 0.5}, "blended")


def unknown_grid(built: race.RaceSetup, shift: list[float] | None = None, noise: float = 50.0):
    drivers = [
        driver.model_copy(
            update={"grid": race.FIELD, "quali_shift_millis": (shift or [0.0] * len(CODES))[i]}
        )
        for i, driver in enumerate(built.drivers)
    ]
    return built.model_copy(
        update={"drivers": drivers, "grid_known": False, "quali_noise_millis": noise}
    )


def test_before_qualifying_the_quick_car_qualifies_and_wins():
    pace = [0.0] + [1.0] * (len(CODES) - 1)
    outcome = run(unknown_grid(setup(pace=pace, pass_base=-6.0, dirty_air=1_500.0)))
    # with the grid unknown nobody starts last by default: the quick car earns the front
    assert outcome.win[0] > 0.8


def test_a_saturday_specialist_starts_ahead_on_a_track_with_no_passing():
    even = setup(pass_base=-6.0, dirty_air=1_500.0, laps=10)
    # same race pace, but the first car loses a second a lap on saturday to everyone else
    slow_saturday = [1_000.0] + [0.0] * (len(CODES) - 1)
    outcome = run(unknown_grid(even, slow_saturday))
    assert outcome.expected_position[0] > sum(outcome.expected_position) / len(CODES)


def test_a_known_grid_draws_exactly_what_it_did_before():
    # the backtest runs on the actual grid, and its committed numbers must not move
    built = setup(pace=[0.1 * i for i in range(len(CODES))])
    assert built.grid_known
    assert (
        run(built).position == run(built.model_copy(update={"quali_noise_millis": 99.0})).position
    )


def test_a_field_bigger_than_twenty_gets_every_place():
    codes = [f"X{index:02d}" for index in range(22)]
    base = setup()
    drivers = [
        base.drivers[0].model_copy(update={"driver_code": code, "grid": index + 1})
        for index, code in enumerate(codes)
    ]
    retirement = base.retirement.model_copy(update={"per_lap": {code: 0.0 for code in codes}})
    outcome = run(base.model_copy(update={"drivers": drivers, "retirement": retirement}), paths=300)
    grid = outcome.probabilities()
    # eleven teams, twenty-two cars: twenty-first and twenty-second are real places, not a
    # pile-up at twentieth
    assert grid.shape == (22, 22)
    assert np.allclose(grid.sum(axis=0), 1.0)
    assert grid[:, 20:].sum() == pytest.approx(2.0)
