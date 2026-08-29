from datetime import date

import numpy as np

from pitadvisor.features.reliability import Cause, Hazard, ReliabilityFit
from pitadvisor.sim import dnf

AS_OF = date(2025, 1, 1)


def hazard(key: str, cause: Cause, per_lap: float) -> Hazard:
    return Hazard(
        key=key,
        cause=cause,
        per_lap=per_lap,
        interval_low=per_lap / 2,
        interval_high=per_lap * 2,
        weighted_failures=1.0,
        weighted_laps=1.0 / max(per_lap, 1e-9),
    )


def fitted(field: float = 0.002, collision: float = 0.0008) -> ReliabilityFit:
    return ReliabilityFit(
        as_of=AS_OF,
        half_life_events=10.0,
        field_rates={str(Cause.ANY): field, str(Cause.COLLISION): collision},
        cause_coverage=0.5,
        teams=[
            hazard("solid", Cause.ANY, 0.001),
            hazard("fragile", Cause.ANY, 0.004),
        ],
        drivers=[
            hazard("careful", Cause.COLLISION, 0.0002),
            hazard("wild", Cause.COLLISION, 0.003),
        ],
        events_used=40,
        events_dropped=0,
    )


def test_the_car_carries_the_failures_and_the_driver_moves_it():
    model = dnf.build(
        fitted(),
        {"AAA": "solid", "BBB": "solid"},
        {"AAA": "careful", "BBB": "wild"},
    )
    assert model.per_lap["AAA"] < 0.001 < model.per_lap["BBB"]


def test_a_collision_is_not_counted_twice():
    """Adding the driver's own rate to a team rate that already contains it would double
    every collision in the sport, so what is added is the difference from the field."""
    model = dnf.build(fitted(), {"AAA": "fragile"}, {"AAA": "wild"})
    assert model.per_lap["AAA"] == 0.004 + 0.003 - 0.0008


def test_a_hazard_never_goes_to_zero_or_below():
    model = dnf.build(fitted(field=0.002, collision=0.05), {"AAA": "solid"}, {"AAA": "careful"})
    assert model.per_lap["AAA"] > 0.0


def test_an_unknown_seat_falls_back_to_the_field():
    model = dnf.build(fitted(), {"AAA": "nobody"}, {})
    assert model.per_lap["AAA"] == 0.002


def test_the_sampled_retirement_rate_matches_the_hazard_it_was_given():
    model = dnf.build(fitted(), {"AAA": "solid", "BBB": "fragile"}, {})
    drawn = dnf.sample(model, ["AAA", "BBB"], laps=60, paths=20_000, rng=np.random.default_rng(5))
    assert drawn.shape == (20_000, 2)
    survived = (drawn > 60).mean(axis=0)
    assert survived[0] == np.float64(np.round(survived[0], 10))
    assert abs(survived[0] - (1 - 0.001) ** 60) < 0.02
    assert abs(survived[1] - (1 - 0.004) ** 60) < 0.02


def test_a_car_that_finishes_is_marked_past_the_end():
    model = dnf.build(fitted(field=1e-9), {"AAA": "nobody"}, {})
    drawn = dnf.sample(model, ["AAA"], laps=30, paths=100, rng=np.random.default_rng(2))
    assert (drawn == 31).all()
