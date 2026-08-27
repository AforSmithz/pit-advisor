from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.weather import (
    PRIOR_SD,
    NoForecastError,
    Scenario,
    ScenarioWeights,
    adjusted,
    hourly_wet,
    scenario,
    wet_form,
)

START = datetime(2024, 5, 5, 13, 0, tzinfo=UTC)
INGESTED = datetime(2024, 5, 4, 6, 0, tzinfo=UTC)


def hours(
    plan: list[tuple[float, float | None]],
    ingested: datetime = INGESTED,
    forecast: bool = True,
) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "circuit_id": "catalunya",
                "observed_at": START + timedelta(hours=index),
                "ingested_at": ingested,
                "is_forecast": forecast,
                "precipitation_mm": millimetres,
                "precipitation_probability": probability,
            }
            for index, (millimetres, probability) in enumerate(plan)
        ]
    )


def test_a_dry_forecast_puts_everything_on_dry():
    weights = scenario(hours([(0.0, 0.0), (0.0, 5.0), (0.0, 10.0)]), START)
    assert weights.dry == 1.0
    assert weights.wet == 0.0
    assert weights.mixed == 0.0


def test_a_settled_downpour_puts_everything_on_wet():
    weights = scenario(hours([(4.0, 100.0), (5.0, 100.0), (4.5, 100.0)]), START)
    assert weights.wet == 1.0
    assert weights.dry == 0.0


def test_rain_arriving_mid_race_reads_as_mixed_not_as_half_a_wet_race():
    weights = scenario(hours([(0.0, 0.0), (2.0, 80.0), (3.0, 90.0)]), START)
    assert weights.mixed == pytest.approx(0.9)
    assert weights.dry == pytest.approx(0.1)
    assert weights.wet == 0.0


def test_the_three_weights_are_a_distribution():
    for plan in (
        [(0.0, 0.0), (2.0, 80.0)],
        [(4.0, 100.0), (5.0, 100.0)],
        [(0.1, 40.0), (3.0, 60.0)],
    ):
        weights = scenario(hours(plan), START)
        assert sum(weights.as_dict().values()) == pytest.approx(1.0)
        assert all(value >= 0.0 for value in weights.as_dict().values())


def test_drizzle_that_never_reaches_the_crossover_is_a_dry_hour():
    # 90% chance of 0.2 mm is 0.22 mm if it rains, which is a damp track on slicks
    assert hourly_wet(90.0, 0.2) == 0.0
    assert hourly_wet(90.0, 2.0) == pytest.approx(0.9)


def test_a_certainty_with_no_probability_column_falls_back_to_intensity():
    assert hourly_wet(None, 2.0) == 1.0
    assert hourly_wet(None, 0.1) == 0.0


def test_only_the_hours_inside_the_session_window_count():
    plan = [(4.0, 100.0)] + [(0.0, 0.0)] * 3
    early = hours(plan).with_columns(pl.col("observed_at") - timedelta(hours=1))
    weights = scenario(early, START)
    assert weights.hours == 3
    assert weights.dry == 1.0


def test_a_window_nothing_covers_raises_rather_than_guessing():
    with pytest.raises(NoForecastError):
        scenario(hours([(0.0, 0.0)]), START + timedelta(days=2))


def test_the_newest_snapshot_wins_and_an_older_one_can_be_replayed():
    stale = hours([(4.0, 100.0), (4.0, 100.0)], ingested=INGESTED)
    fresh = hours([(0.0, 0.0), (0.0, 0.0)], ingested=INGESTED + timedelta(days=1))
    both = pl.concat([stale, fresh])
    assert scenario(both, START).dry == 1.0
    replayed = scenario(both, START, as_of=INGESTED + timedelta(hours=1))
    assert replayed.wet == 1.0
    assert replayed.snapshot_at == INGESTED


def season(
    wet_penalty: dict[str, float],
    wet_rounds: tuple[int, ...] = (4, 11),
    events: int = 20,
    noise: float = 0.15,
    seed: int = 5,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = date(2024, 3, 3)
    for index in range(events):
        held = start + timedelta(days=14 * index)
        wet = (index + 1) in wet_rounds
        for code, penalty in wet_penalty.items():
            rows.append(
                {
                    "season": 2024,
                    "round": index + 1,
                    "race_date": held,
                    "driver_code": code,
                    "constructor_id": f"team_{code[0]}",
                    "is_wet": wet,
                    "value": (penalty if wet else 0.0) + rng.normal(0.0, noise),
                }
            )
    return pl.DataFrame(rows)


def test_a_known_rain_master_comes_back_with_a_negative_delta():
    frame = season({"AAA": -1.5, "BBB": 1.5, "CCC": 0.0}, wet_rounds=(3, 7, 11, 15))
    fitted = wet_form(frame, as_of=date(2025, 1, 1))
    found = {item.key: item for item in fitted.drivers}
    assert found["AAA"].delta < found["CCC"].delta < found["BBB"].delta
    assert found["AAA"].interval_low <= found["AAA"].shrunk_delta <= found["AAA"].interval_high
    assert abs(found["AAA"].delta - -1.5) < 3.0 * found["AAA"].standard_error


def test_the_shrunk_delta_never_overshoots_the_raw_one():
    frame = season({"AAA": -1.5, "BBB": 1.5})
    for item in wet_form(frame, as_of=date(2025, 1, 1)).drivers:
        assert abs(item.shrunk_delta) < abs(item.delta)


def test_one_wet_race_says_nothing_and_falls_all_the_way_back_to_the_prior():
    frame = season({"AAA": -3.0}, wet_rounds=(19,))
    item = wet_form(frame, as_of=date(2025, 1, 1)).drivers[0]
    assert item.wet_sessions == 1
    assert item.shrunk_delta == 0.0
    assert item.standard_error == PRIOR_SD


def test_a_season_with_no_rain_reports_zero_wet_sessions_and_no_deltas():
    frame = season({"AAA": -1.5}, wet_rounds=())
    fitted = wet_form(frame, as_of=date(2025, 1, 1))
    assert fitted.wet_sessions == 0
    assert fitted.drivers == []


def test_a_driver_who_only_ever_raced_in_the_wet_gets_no_delta():
    frame = season({"AAA": -1.5}, wet_rounds=tuple(range(1, 21)))
    assert wet_form(frame, as_of=date(2025, 1, 1)).drivers == []


def test_nothing_after_the_as_of_date_reaches_the_fit():
    frame = season({"AAA": -1.5, "BBB": 1.5})
    cut = wet_form(frame, as_of=date(2024, 5, 1))
    assert cut.events_used == 5
    assert cut.events_dropped == frame.filter(pl.col("race_date") >= date(2024, 5, 1)).height


def test_teams_are_rated_beside_drivers():
    frame = season({"AAA": -1.5, "BBB": 1.5}, wet_rounds=(3, 7, 11, 15))
    fitted = wet_form(frame, as_of=date(2025, 1, 1))
    assert sorted(item.key for item in fitted.teams) == ["team_A", "team_B"]


def weights_of(dry: float, mixed: float, wet: float) -> ScenarioWeights:
    return ScenarioWeights(
        dry=dry,
        mixed=mixed,
        wet=wet,
        hours=3,
        is_forecast=True,
        snapshot_at=INGESTED,
        expected_mm=0.0,
        wettest_hour=wet + mixed,
        driest_hour=wet,
    )


def test_a_dry_forecast_leaves_the_pace_input_alone():
    assert adjusted(0.4, -1.5, weights_of(1.0, 0.0, 0.0)) == pytest.approx(0.4)


def test_a_wet_forecast_applies_the_whole_delta_and_mixed_applies_half():
    assert adjusted(0.4, -1.5, weights_of(0.0, 0.0, 1.0)) == pytest.approx(-1.1)
    assert adjusted(0.4, -1.5, weights_of(0.0, 1.0, 0.0)) == pytest.approx(-0.35)


def test_the_scenario_names_stay_stable_for_the_view_contract():
    assert [str(item) for item in Scenario] == ["dry", "mixed", "wet"]
