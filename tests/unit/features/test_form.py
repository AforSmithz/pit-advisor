from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.form import HALF_LIFE_EVENTS, NoPairingsError, decay, fit, pairings

TEAMS = ("alpha", "bravo", "charlie")


def season(
    truth: dict[str, float],
    lineups: dict[str, tuple[str, str]] | None = None,
    events: int = 16,
    noise: float = 0.05,
    seed: int = 3,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    seats = lineups or {team: (f"{team[0].upper()}1", f"{team[0].upper()}2") for team in TEAMS}
    rows = []
    start = date(2024, 3, 3)
    for index in range(events):
        held = start + timedelta(days=14 * index)
        for team, drivers in seats.items():
            car = rng.normal(0.0, 1.0)
            for code in drivers:
                rows.append(
                    {
                        "season": 2024,
                        "round": index + 1,
                        "race_date": held,
                        "driver_code": code,
                        "constructor_id": team,
                        "value": car + truth[code] + rng.normal(0.0, noise),
                    }
                )
    return pl.DataFrame(rows)


def truth_for(seats: dict[str, tuple[str, str]], gaps: dict[str, float]) -> dict[str, float]:
    return {code: gaps.get(code, 0.0) for drivers in seats.values() for code in drivers}


def test_a_known_teammate_gap_comes_back_inside_its_interval():
    gaps = {"A1": -0.20, "A2": 0.20, "B1": 0.15, "B2": -0.15, "C1": 0.0, "C2": 0.30}
    frame = season(gaps)
    fitted = fit(frame, as_of=date(2025, 1, 1))
    contrast = fitted.contrast("A1", "A2")
    assert contrast.interval_low <= -0.40 <= contrast.interval_high
    assert contrast.faster == "A1"


def test_the_quicker_teammate_is_the_one_with_the_lower_effect():
    gaps = {"A1": -0.20, "A2": 0.20, "B1": 0.15, "B2": -0.15, "C1": 0.0, "C2": 0.30}
    fitted = fit(season(gaps), as_of=date(2025, 1, 1))
    effect = {driver.driver_code: driver.effect for driver in fitted.drivers}
    assert effect["A1"] < effect["A2"]
    assert effect["C1"] < effect["C2"]


def test_a_decayed_event_counts_half_as_much_one_half_life_back():
    assert decay(np.array([0.0]))[0] == pytest.approx(1.0)
    assert decay(np.array([HALF_LIFE_EVENTS]))[0] == pytest.approx(0.5)
    assert decay(np.array([2 * HALF_LIFE_EVENTS]))[0] == pytest.approx(0.25)


def test_nothing_on_or_after_the_prediction_date_reaches_the_fit():
    gaps = {"A1": -0.2, "A2": 0.2, "B1": 0.0, "B2": 0.0, "C1": 0.0, "C2": 0.0}
    frame = season(gaps, events=16)
    cut = date(2024, 3, 3) + timedelta(days=14 * 10)
    fitted = fit(frame, as_of=cut)
    assert fitted.events_used == 10
    assert fitted.events_dropped == 6 * len(TEAMS) * 2


def test_drivers_who_never_shared_a_car_are_not_comparable():
    gaps = {"A1": -0.2, "A2": 0.2, "B1": 0.0, "B2": 0.0, "C1": 0.0, "C2": 0.0}
    fitted = fit(season(gaps), as_of=date(2025, 1, 1))
    assert fitted.components == len(TEAMS)
    component = {driver.driver_code: driver.component for driver in fitted.drivers}
    assert component["A1"] == component["A2"]
    assert component["A1"] != component["B1"]


def test_a_mid_season_swap_joins_two_components():
    seats = {"alpha": ("A1", "A2"), "bravo": ("B1", "B2"), "charlie": ("C1", "C2")}
    gaps = truth_for(seats, {"A1": -0.2, "A2": 0.2})
    early = season(gaps, seats, events=8)
    swapped = {"alpha": ("A1", "B2"), "bravo": ("B1", "A2"), "charlie": ("C1", "C2")}
    late = season(gaps, swapped, events=8, seed=11)
    late = late.with_columns(pl.col("race_date") + timedelta(days=14 * 8))
    fitted = fit(pl.concat([early, late]), as_of=date(2026, 1, 1))
    assert fitted.components == 2


def test_a_car_that_stopped_being_the_same_car_is_flagged_out():
    gaps = {"A1": -0.2, "A2": 0.2, "B1": 0.0, "B2": 0.0, "C1": 0.0, "C2": 0.0}
    frame = season(gaps)
    damaged = frame.with_columns(
        pl.when((pl.col("round") == 5) & (pl.col("driver_code") == "A2"))
        .then(pl.col("value") + 6.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    fitted = fit(damaged, as_of=date(2025, 1, 1))
    assert fitted.flagged_pairs == 1
    assert fitted.pairs == frame.height // 2 - 1


def test_a_field_of_single_car_teams_has_nothing_to_compare():
    frame = pl.DataFrame(
        [
            {
                "season": 2024,
                "round": 1,
                "race_date": date(2024, 3, 3),
                "driver_code": "A1",
                "constructor_id": "alpha",
                "value": 0.4,
            }
        ]
    )
    with pytest.raises(NoPairingsError):
        fit(frame, as_of=date(2025, 1, 1))


def test_one_pairing_per_team_event():
    gaps = {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0, "C1": 0.0, "C2": 0.0}
    frame = season(gaps, events=4)
    assert pairings(frame).height == 4 * len(TEAMS)
