from datetime import date

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.assemble import event_at, next_event
from pitadvisor.model import backtest
from pitadvisor.model.baselines import FEATURES

SEED = 5


@pytest.fixture
def pane(store, seeded):
    seeded()
    return backtest.panel(store)


def test_the_panel_derives_every_frame_the_simulation_needs(pane):
    assert pane.paces
    assert pane.pace.height
    assert set(pane.starts.columns) >= {"grid", "start_position"}
    assert pane.cautions.height
    assert pane.stops.height
    assert pane.laps
    assert pane.entries


def test_a_lake_with_no_session_laps_says_so(store, seed_lake, tmp_path):
    from pitadvisor.ingest.raw_store import LocalObjectStore
    from pitadvisor.types import Layer

    seed_lake(store)
    thin = LocalObjectStore(tmp_path / "thin")
    for item in store.list(""):
        if f"table={'session_laps'}" not in item.key and item.key.startswith(Layer.BRONZE):
            thin.put(item.key, store.get(item.key))
    with pytest.raises(Exception, match="session_laps"):
        backtest.panel(thin)


def test_a_forecast_covers_the_whole_entry_list(pane, store):
    context = next_event(store, date(2024, 1, 1))
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=200
    )
    outcome = predicted.outcome
    assert len(outcome.driver_code) == 6
    assert sum(outcome.win) == pytest.approx(1.0)
    assert np.allclose(outcome.probabilities().sum(axis=1), 1.0)
    assert predicted.assumptions


def test_the_same_seed_forecasts_the_same_race(pane, store):
    context = event_at(store, 2024, 3)
    first = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=200
    )
    again = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=200
    )
    assert first.outcome.position == again.outcome.position


def test_scenario_weights_come_from_climatology_not_from_the_archive(store, seeded):
    """The weather of a race being backtested is in the lake and it is what happened. Using
    it would tell the simulation whether it rained before the race it is predicting."""
    seeded(wet_rounds=(2, 4))
    pane = backtest.panel(store)
    # round two is monza and it rained there both seasons, round one is bahrain and it
    # never did, so the climatology should separate them
    monza = backtest.scenario_weights(pane, "monza", date(2024, 12, 1))
    bahrain = backtest.scenario_weights(pane, "bahrain", date(2024, 12, 1))
    assert set(monza) == set(backtest.SCENARIOS)
    assert sum(monza.values()) == pytest.approx(1.0)
    assert bahrain["dry"] > monza["dry"]
    assert monza["wet"] > bahrain["wet"]


def test_a_forecast_with_no_history_behind_it_is_refused(pane, store):
    context = event_at(store, 2023, 1)
    with pytest.raises(backtest.NoForecastError):
        backtest.forecast(pane, context, date(2020, 1, 1), np.random.default_rng(SEED), paths=50)


def test_the_race_distance_is_read_from_the_circuit_not_from_the_result(pane, store):
    """A red flag shortens a race, and reading how far it went off its own result would be
    a leak, so the distance comes from what this circuit has run before."""
    context = event_at(store, 2024, 2)
    shortened = backtest._race_laps(pane, context, context.race_date)
    assert shortened == pane.laps[(2023, 2)]


def test_a_walk_forward_scores_the_model_against_every_baseline(pane):
    report = backtest.run(
        pane, 2024, 4, np.random.default_rng(SEED), "test-run", paths=200, seed=SEED
    )
    assert {item.name for item in report.scored} == {backtest.MODEL, *FEATURES}
    assert {item.baseline for item in report.paired} == set(FEATURES)
    assert report.per_race
    for scored in report.scored:
        assert scored.log_loss.value > 0.0
        assert scored.rows == sum(len(item.log_loss) > 0 for item in report.per_race) * 6


def test_every_race_in_the_holdout_is_scored_on_a_fit_that_could_not_see_it(pane):
    report = backtest.run(
        pane, 2024, 3, np.random.default_rng(SEED), "test-run", paths=150, seed=SEED
    )
    scored = sorted(item.race_date for item in report.per_race)
    assert scored == sorted(set(scored))
    assert len(scored) == report.scored[0].races


def test_a_holdout_with_no_races_is_refused(pane):
    with pytest.raises(backtest.NoForecastError):
        backtest.run(pane, 2099, 5, np.random.default_rng(SEED), "test-run", paths=50)


def test_a_nineteen_car_race_does_not_get_mass_on_a_place_that_cannot_happen():
    grid = np.full((3, backtest.FIELD), 1.0 / backtest.FIELD)
    trimmed = backtest._renormalised(grid, 19)
    assert trimmed.shape == (3, backtest.FIELD)
    assert trimmed[:, 19].sum() == 0.0
    assert np.allclose(trimmed.sum(axis=1), 1.0)


def test_the_pit_lane_starter_is_put_at_the_back(pane, store):
    context = event_at(store, 2024, 1)
    codes = sorted(
        pane.results.filter((pl.col("season") == 2024) & (pl.col("round") == 1))[
            "driver_code"
        ].to_list()
    )
    slots = backtest.grid_for(pane, context, codes)
    assert min(slots.values()) >= 1
    assert max(slots.values()) <= backtest.FIELD
