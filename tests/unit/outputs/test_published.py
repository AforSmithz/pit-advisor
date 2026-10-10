import dataclasses
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from pitadvisor.features.assemble import event_at
from pitadvisor.model import backtest
from pitadvisor.outputs import published
from pitadvisor.outputs.view_contracts import forecast_view


def issued(built, round_=6, hours_before=20, run_id="run-1"):
    pane = backtest.panel(built.store)
    context = event_at(built.store, 2024, round_)
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(5), paths=200
    )
    seats, _ = backtest.seats_for(pane, context, context.race_date)
    grid = backtest.grid_for(pane, context, predicted.outcome.driver_code)
    lights = datetime(
        context.race_date.year, context.race_date.month, context.race_date.day, 13, tzinfo=UTC
    )
    view = forecast_view(
        predicted,
        context,
        run_id,
        seats,
        grid,
        generated_at=lights - timedelta(hours=hours_before),
    )
    return pane, view, lights


def no_sprints():
    return pl.DataFrame(
        schema={
            "season": pl.Int64,
            "round": pl.Int64,
            "driver_code": pl.String,
            "position": pl.Int64,
        }
    )


def test_a_forecast_made_before_lights_out_is_kept_once(seeded):
    built = seeded()
    _, view, lights = issued(built)
    key = published.archive(built.store, view, "race", lights)
    assert key == (f"views/history/season=2024/round=06/race-after_qualifying-{view.run_id}.json")
    assert built.store.exists(key)
    assert published.archive(built.store, view, "race", lights) is None


def test_a_forecast_made_after_the_start_is_not_a_prediction(seeded):
    built = seeded()
    _, view, lights = issued(built, hours_before=-1)
    assert published.archive(built.store, view, "race", lights) is None
    assert published.archive(built.store, view, "race", None) is None
    assert list(built.store.list(published.HISTORY)) == []


def test_a_naive_start_time_is_read_as_utc(seeded):
    built = seeded()
    _, view, lights = issued(built, hours_before=1)
    assert published.archive(built.store, view, "race", lights.replace(tzinfo=None)) is not None


def test_a_sprint_forecast_is_kept_under_its_own_kind(seeded):
    built = seeded()
    _, view, lights = issued(built)
    key = published.archive(built.store, view, "sprint", lights)
    assert key is not None
    assert "/sprint-sprint-" in key


def test_kept_forecasts_are_scored_against_the_result_and_the_baselines(seeded):
    built = seeded()
    pane, view, lights = issued(built)
    published.archive(built.store, view, "race", lights)
    scores = published.score(built.store, pane, no_sprints())
    assert len(scores) == 1
    item = scores[0]
    assert (item.season, item.round, item.stage) == (2024, 6, "after_qualifying")
    assert set(item.log_loss) == {"simulation", "grid", "standings", "last_race"}
    assert all(np.isfinite(value) and value > 0 for value in item.log_loss.values())
    assert "1 forecasts" in published.summarise(scores)


def test_before_qualifying_there_is_no_grid_to_compare_with(seeded):
    built = seeded()
    pane, view, lights = issued(built)
    early = view.model_copy(update={"grid_sampled": True, "run_id": "run-2"})
    published.archive(built.store, early, "race", lights)
    (item,) = published.score(built.store, pane, no_sprints())
    assert item.stage == "before_qualifying"
    assert "grid" not in item.log_loss


def test_a_forecast_whose_race_has_not_run_waits(seeded):
    built = seeded()
    pane, view, lights = issued(built)
    published.archive(built.store, view, "race", lights)
    unrun = dataclasses.replace(pane, results=pane.results.filter(pl.col("round") != 6))
    assert published.score(built.store, unrun, no_sprints()) == []
    assert published.summarise([]) == "nothing published has a result yet\n"
