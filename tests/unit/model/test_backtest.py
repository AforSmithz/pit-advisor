import dataclasses
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


def test_a_22_car_race_keeps_all_22_places_and_a_20_car_race_pads_to_it():
    wide = backtest._renormalised(np.full((2, 22), 1.0 / 22), 22, 22)
    assert np.allclose(wide.sum(axis=1), 1.0)
    assert (wide[:, 20:] > 0).all()
    narrow = backtest._renormalised(np.full((2, 20), 1.0 / 20), 20, 22)
    assert narrow.shape == (2, 22)
    assert narrow[:, 20:].sum() == 0.0
    assert np.allclose(narrow.sum(axis=1), 1.0)


def test_a_race_on_the_calendar_that_has_not_run_does_not_shrink_the_holdout(pane):
    last = pane.events.sort("race_date").tail(1)
    unrun = last.with_columns(
        (pl.col("round") + 100).alias("round"),
        (pl.col("race_date") + pl.duration(days=30)).alias("race_date"),
    )
    ahead = dataclasses.replace(pane, events=pl.concat([pane.events, unrun]))
    before = backtest.run(pane, 2024, 3, np.random.default_rng(SEED), "a", paths=100)
    after = backtest.run(ahead, 2024, 3, np.random.default_rng(SEED), "b", paths=100)
    assert [item.race_date for item in after.per_race] == [
        item.race_date for item in before.per_race
    ]


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


def before_qualifying(pane, context):
    import dataclasses

    # the race has not run: no grid, no result, and no qualifying either
    held = (pl.col("season") == context.season) & (pl.col("round") == context.round)
    return dataclasses.replace(
        pane, results=pane.results.filter(~held), quali=pane.quali.filter(~held)
    )


def test_before_qualifying_the_grid_is_sampled_not_set_to_last(pane, store):
    context = event_at(store, 2024, 3)
    early = before_qualifying(pane, context)
    built = backtest.setup(early, context, context.race_date, "dry")
    assert not built.grid_known
    # the synthetic lake converts quali to race with no noise at all, so the measured spread
    # is whatever the past says, which can be zero, and never one measured on later races
    expected = backtest.quali_anchor_sd(early, context.race_date)
    assert built.quali_noise_millis == pytest.approx(built.reference_millis * expected / 100.0)
    predicted = backtest.forecast(
        early, context, context.race_date, np.random.default_rng(SEED), paths=300
    )
    assert predicted.grid_sampled
    assert sum(predicted.outcome.win) == pytest.approx(1.0)
    # every car starting from the back would flatten the field; a sampled grid does not
    assert max(predicted.outcome.win) > 1.5 / len(predicted.outcome.driver_code)


def test_with_the_grid_known_nothing_is_sampled(pane, store):
    context = event_at(store, 2024, 3)
    built = backtest.setup(pane, context, context.race_date, "dry")
    assert built.grid_known
    assert built.quali_noise_millis == 0.0
    assert not backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=100
    ).grid_sampled


def test_quali_shifts_fall_back_to_the_field(pane, store):
    context = event_at(store, 2024, 3)
    shifts = backtest.quali_shifts(pane, context.race_date, ["NOBODY"])
    assert set(shifts) == {"NOBODY"}


def test_an_unrun_race_takes_the_last_race_entry_list(pane, store):
    context = event_at(store, 2024, 3)
    early = before_qualifying(pane, context)
    seats, _ = backtest.seats_for(early, context, context.race_date)
    before = early.results.filter(pl.col("race_date") < context.race_date).drop_nulls("driver_code")
    last = before.filter(pl.col("race_date") == before["race_date"].max())
    assert seats == {
        str(row["driver_code"]): str(row["constructor_id"]) for row in last.iter_rows(named=True)
    }
    # not every pairing the lake has ever seen
    assert len(seats) == last.height


def test_the_race_day_spread_is_measured_on_the_past_only(pane):
    first = date(2024, 1, 1)
    calm = [{"driver_code": "AAA", "race_date": first, "is_wet": False, "value": 0.1}] * 5
    wild = [
        {"driver_code": "AAA", "race_date": date(2024, 6, day), "is_wet": False, "value": value}
        for day, value in zip(range(1, 6), [3.0, -3.0, 2.0, -2.0, 4.0], strict=True)
    ]
    rows = [{**row, "race_date": date(2024, 1, index + 1)} for index, row in enumerate(calm)]
    later = dataclasses.replace(pane, pace=pl.DataFrame(rows + wild))
    # the wild june races are after the cutoff, so they say nothing about march
    assert backtest.race_day_sd(later, date(2024, 3, 1)) == pytest.approx(0.0)
    assert backtest.race_day_sd(later, date(2024, 7, 1)) > 1.0
    assert backtest.race_day_sd(later, first) == backtest.DEFAULT_RACE_DAY_SD


def test_the_quali_anchor_spread_is_measured_on_the_past_only(pane):
    assert backtest.quali_anchor_sd(pane, date(2000, 1, 1)) == backtest.DEFAULT_QUALI_ANCHOR_SD


def test_a_circuits_first_race_takes_its_distance_from_the_past_not_the_lake(pane, store):
    context = event_at(store, 2024, 4)
    first = context.model_copy(update={"circuit_id": "nowhere"})
    later = {
        key: 999
        for key, row in zip(
            pane.events.select("season", "round").iter_rows(),
            pane.events.iter_rows(named=True),
            strict=True,
        )
        if row["race_date"] >= context.race_date
    }
    padded = dataclasses.replace(pane, laps={**pane.laps, **later})
    assert backtest._race_laps(padded, first, context.race_date) == backtest._race_laps(
        pane, first, context.race_date
    )
    assert backtest._race_laps(padded, first, context.race_date) < 999
