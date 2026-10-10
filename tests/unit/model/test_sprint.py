from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.assemble import event_at
from pitadvisor.model import backtest, sprint

SEED = 5


@pytest.fixture
def pane(store, seeded):
    seeded()
    return backtest.panel(store)


def sprints_from(pane, rounds):
    """Synthetic sprints: each chosen round's race result, run the day before the race."""
    rows = pane.results.filter(
        pl.struct("season", "round").is_in([{"season": s, "round": r} for s, r in rounds])
    )
    return rows.with_columns(
        (pl.col("race_date") - timedelta(days=1)).alias("race_date"),
        pl.lit(17).alias("laps_completed"),
    ).select(sprint.COLUMNS)


def test_a_sprint_is_dated_the_day_it_ran():
    raw = pl.DataFrame(
        {
            "season": [2024],
            "round": [3],
            "driver_id": ["a"],
            "driver_code": ["AAA"],
            "constructor_id": ["alpha"],
            "grid": [1],
            "position": [1],
            "points": [8],
            "laps_completed": [19],
        }
    )
    schedule = pl.DataFrame({"season": [2024], "round": [3], "sprint_date": [date(2024, 4, 6)]})
    frame = sprint.sprint_frame(raw, schedule)
    assert frame["race_date"].to_list() == [date(2024, 4, 6)]
    assert frame.columns == list(sprint.COLUMNS)
    assert sprint.sprint_frame(None, schedule).height == 0


def test_the_morning_of_a_sprint_has_no_race_result_for_the_weekend(pane, store):
    context = event_at(store, 2023, 3)
    morning = sprint.before_sprint(pane, context)
    held = (pl.col("season") == 2023) & (pl.col("round") == 3)
    assert morning.results.filter(held).height == 0
    assert morning.quali.filter(held).height == pane.quali.filter(held).height


def test_from_2024_main_qualifying_is_also_hidden_from_the_sprint(pane, store):
    context = event_at(store, 2024, 3)
    morning = sprint.before_sprint(pane, context)
    assert (2024, 3) not in morning.qualified


def test_a_circuit_with_no_sprint_runs_a_third_of_its_race(pane, store):
    context = event_at(store, 2024, 3)
    none = sprints_from(pane, []).head(0)
    expected = max(round(backtest.race_laps(pane, context, context.race_date) / 3), 1)
    assert sprint.sprint_laps(none, pane, context) == expected


def test_a_sprint_forecast_has_no_stops_and_covers_the_sprint_grid(pane, store):
    context = event_at(store, 2024, 4)
    sprints = sprints_from(pane, [(2024, 4)])
    outcome = sprint.forecast(
        pane,
        sprints,
        context,
        context.race_date - timedelta(days=1),
        np.random.default_rng(SEED),
        paths=150,
    )
    assert set(outcome.driver_code) == set(sprints["driver_code"].to_list())
    assert np.allclose(outcome.probabilities().sum(axis=1), 1.0)


def test_the_sprint_walk_scores_the_model_against_the_baselines(pane):
    rounds = [(2023, r) for r in range(2, 7)] + [(2024, r) for r in range(1, 7)]
    report = sprint.run(
        pane,
        sprints_from(pane, rounds),
        np.random.default_rng(SEED),
        "t",
        paths=150,
        seed=SEED,
        warmup=3,
    )
    assert report.stage == "sprint"
    assert report.per_race
    assert {item.baseline for item in report.paired} == {"grid", "standings", "last_race"}
