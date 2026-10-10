import dataclasses
from datetime import UTC, date, datetime, timedelta

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


def knockout_for(pane, season, round_):
    codes = sorted(
        pane.results.filter((pl.col("season") == season) & (pl.col("round") == round_))[
            "driver_code"
        ].to_list()
    )
    return pl.DataFrame(
        {
            "season": [season] * len(codes),
            "round": [round_] * len(codes),
            "driver_code": codes,
            "reach": [3 if i < 10 else 2 if i < 15 else 1 for i in range(len(codes))],
            "best_millis": [90_000.0 + 50.0 * i for i in range(len(codes))],
            "percent": [0.05 * i for i in range(len(codes))],
        }
    )


def test_the_live_grid_is_the_knockout_order(pane, store):
    context = event_at(store, 2024, 4)
    assert sprint.live_grid(pane, context) == {}
    order = knockout_for(pane, 2024, 4)
    planted = dataclasses.replace(pane, sprint_quali=order)
    grid = sprint.live_grid(planted, context)
    assert sorted(grid.values()) == list(range(1, order.height + 1))
    assert grid[order["driver_code"][0]] == 1


def test_timing_reads_the_sprint_off_the_calendar(pane, store):
    context = event_at(store, 2024, 4)
    races = pl.DataFrame(
        {
            "season": [2024, 2024],
            "round": [4, 5],
            "sprint_date": [date(2024, 4, 20), None],
            "sprint_utc": [datetime(2024, 4, 20, 3), None],
        }
    )
    day, when = sprint.timing(races, context)
    assert day == date(2024, 4, 20)
    assert when == datetime(2024, 4, 20, 3, tzinfo=UTC)
    assert sprint.timing(races.filter(pl.col("round") == 5), context) == (None, None)
    assert sprint.timing(None, context) == (None, None)


def test_the_sprint_state_walks_from_no_sprint_to_run(pane, store):
    context = event_at(store, 2024, 4)
    races = pl.DataFrame(
        {
            "season": [2024],
            "round": [4],
            "sprint_date": [date(2024, 4, 20)],
            "sprint_utc": [datetime(2024, 4, 20, 3, tzinfo=UTC)],
        }
    )
    before = datetime(2024, 4, 20, 1, tzinfo=UTC)
    empty = sprints_from(pane, [])
    assert sprint.state(empty, pane, context, races.head(0), before) == "no_sprint"
    assert sprint.state(empty, pane, context, races, before) == "awaiting_grid"
    planted = dataclasses.replace(pane, sprint_quali=knockout_for(pane, 2024, 4))
    assert sprint.state(empty, planted, context, races, before) == "forecast"
    after = datetime(2024, 4, 20, 4, tzinfo=UTC)
    assert sprint.state(empty, planted, context, races, after) == "run"
    held = sprints_from(pane, [(2024, 4)])
    assert sprint.state(held, planted, context, races, before) == "run"


def test_the_live_sprint_forecast_pays_the_top_eight_and_starts_from_the_knockout(pane, store):
    context = event_at(store, 2024, 4)
    planted = dataclasses.replace(pane, sprint_quali=knockout_for(pane, 2024, 4))
    day = context.race_date - timedelta(days=1)
    empty = sprints_from(pane, []).head(0)
    predicted = sprint.live(planted, empty, context, day, np.random.default_rng(SEED), None, 400)
    outcome = predicted.outcome
    assert predicted.as_of == day
    assert not predicted.grid_sampled
    assert predicted.laps == sprint.sprint_laps(empty, planted, context)
    for index in range(len(outcome.driver_code)):
        assert outcome.points[index] == pytest.approx(sum(outcome.position[index][:8]))
    assert sum(outcome.win) == pytest.approx(1.0)
    with pytest.raises(backtest.NoForecastError):
        sprint.live(pane, empty, context, day, np.random.default_rng(SEED), None, 400)
