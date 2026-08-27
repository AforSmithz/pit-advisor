from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.reliability import Cause, classify, exposed, fit

RACE_LAPS = 55


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Finished", None),
        ("+1 Lap", None),
        ("+3 Laps", None),
        ("Lapped", None),
        ("Did not start", None),
        ("Withdrew", None),
        ("Disqualified", None),
        ("Engine", Cause.POWER_UNIT),
        ("Water pressure", Cause.POWER_UNIT),
        ("Gearbox", Cause.MECHANICAL),
        ("Puncture", Cause.MECHANICAL),
        ("Collision damage", Cause.COLLISION),
        ("Accident", Cause.COLLISION),
        ("Retired", Cause.UNKNOWN),
        ("Vapourised", Cause.UNKNOWN),
    ],
)
def test_a_status_lands_in_one_class(status, expected):
    assert classify(status) is expected


def test_a_car_that_never_started_is_not_exposure():
    frame = pl.DataFrame(
        [
            {"status": "Did not start", "laps_completed": 0, "driver_id": "a"},
            {"status": "Finished", "laps_completed": 55, "driver_id": "b"},
        ]
    )
    assert exposed(frame)["driver_id"].to_list() == ["b"]


def simulate(hazard: float, races: int = 30, cars: int = 20, seed: int = 5) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = date(2024, 3, 3)
    for index in range(races):
        held = start + timedelta(days=14 * index)
        for car in range(cars):
            failed_at = rng.geometric(hazard)
            retired = failed_at <= RACE_LAPS
            rows.append(
                {
                    "season": 2024,
                    "round": index + 1,
                    "race_date": held,
                    "driver_id": f"d{car}",
                    "constructor_id": f"t{car // 2}",
                    "status": "Engine" if retired else "Finished",
                    "laps_completed": int(failed_at) if retired else RACE_LAPS,
                }
            )
    return pl.DataFrame(rows)


def test_a_known_per_lap_hazard_comes_back_inside_its_interval():
    hazard = 0.004
    fitted = fit(simulate(hazard), as_of=date(2025, 1, 1), half_life=1000.0)
    pooled = [team for team in fitted.teams if team.cause is Cause.ANY]
    covered = sum(team.interval_low <= hazard <= team.interval_high for team in pooled)
    assert covered >= len(pooled) - 1
    assert fitted.field_rates["any"] == pytest.approx(hazard, rel=0.25)


def test_every_estimate_sits_inside_its_own_interval():
    fitted = fit(simulate(0.004), as_of=date(2025, 1, 1))
    for hazard in fitted.teams + fitted.drivers:
        assert hazard.interval_low <= hazard.per_lap <= hazard.interval_high


def test_nothing_on_or_after_the_prediction_date_reaches_the_fit():
    frame = simulate(0.004, races=30)
    cut = date(2024, 3, 3) + timedelta(days=14 * 12)
    fitted = fit(frame, as_of=cut)
    assert fitted.events_used == 12
    assert fitted.events_dropped == 18 * 20


def test_an_unnamed_retirement_shows_up_as_missing_cause_coverage():
    frame = simulate(0.02).with_columns(
        pl.when((pl.col("status") == "Engine") & (pl.col("round") > 15))
        .then(pl.lit("Retired"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    fitted = fit(frame, as_of=date(2025, 1, 1), half_life=1000.0)
    assert 0.2 < fitted.cause_coverage < 0.8


def test_a_team_with_no_history_is_pulled_towards_the_field():
    frame = simulate(0.004)
    newcomer = frame.filter((pl.col("constructor_id") == "t0") & (pl.col("round") == 30))
    fitted = fit(
        pl.concat([frame.filter(pl.col("constructor_id") != "t0"), newcomer]),
        as_of=date(2026, 1, 1),
    )
    thin = next(t for t in fitted.teams if t.key == "t0" and t.cause is Cause.ANY)
    assert thin.weighted_laps < 200
    assert abs(thin.per_lap - fitted.field_rates["any"]) < fitted.field_rates["any"]
