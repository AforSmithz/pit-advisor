import json
from datetime import date

import numpy as np
import polars as pl

from pitadvisor.features.assemble import event_at
from pitadvisor.model import backtest, calibrate
from pitadvisor.model.baselines import FEATURES
from pitadvisor.outputs.view_contracts import (
    calibration_view,
    emit,
    evidence_from,
    forecast_view,
    view_key,
)

SEED = 17


def test_a_synthetic_lake_walks_from_bronze_to_both_forecast_artifacts(store, seeded, tmp_path):
    """The whole of P5 end to end: bronze in, a fitted simulation, a time-forward backtest
    against every baseline, the committed artifacts, and the two views the dashboard reads."""
    seeded(wet_rounds=(3,))
    pane = backtest.panel(store)
    assert pane.paces
    assert pane.entries

    report = backtest.run(
        pane, 2024, 4, np.random.default_rng(SEED), "integration", paths=300, seed=SEED
    )
    assert {item.name for item in report.scored} == {backtest.MODEL, *FEATURES}
    assert all(item.log_loss.value > 0 for item in report.scored)
    assert all(item.log_loss.draws > 0 for item in report.scored)

    written = calibrate.write(report, tmp_path / "results")
    assert all(path.exists() for path in written)
    again = backtest.Report.model_validate_json(
        (tmp_path / "results" / calibrate.REPORT).read_text()
    )
    assert again.beats_baselines == report.beats_baselines

    context = event_at(store, 2024, 6)
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=300
    )
    seats, _ = backtest.seats_for(pane, context, context.race_date)
    grid = backtest.grid_for(pane, context, predicted.outcome.driver_code)
    views = [
        forecast_view(predicted, context, "integration", seats, grid, evidence_from(report)),
        calibration_view(report),
    ]
    for view in views:
        emit(store, view)
        stored = json.loads(store.get(view_key(view.view)).decode())
        assert stored["view"] == view.view
        assert stored["run_id"]


def test_no_fit_behind_a_forecast_has_seen_the_race_it_predicts(store, seeded):
    """The leak test. Deleting everything from the race forward must not change a forecast
    made as of that race, because nothing from that day was allowed into the fits."""
    built = seeded()
    pane = backtest.panel(store)
    context = event_at(store, 2024, 4)

    whole = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=400
    )
    truncated = backtest.Panel(
        events=pane.events,
        results=pane.results.filter(
            (pl.col("race_date") < context.race_date)
            | ((pl.col("season") == 2024) & (pl.col("round") == 4))
        ),
        paces=[
            item for item in pane.paces if built.held(item.season, item.round) < context.race_date
        ],
        pace=pane.pace.filter(pl.col("race_date") < context.race_date),
        quali=pane.quali.filter(pl.col("race_date") <= context.race_date),
        quali_events=[item for item in pane.quali_events if item[0] <= context.race_date],
        starts=pane.starts.filter(pl.col("race_date") < context.race_date),
        cautions=pane.cautions.filter(pl.col("race_date") < context.race_date),
        passes=pane.passes.filter(pl.col("race_date") < context.race_date),
        traffic=pane.traffic.filter(pl.col("race_date") < context.race_date),
        stops=pane.stops.filter(pl.col("race_date") < context.race_date),
        degradation=pane.degradation.filter(pl.col("race_date") < context.race_date),
        benchmarks=pane.benchmarks.filter(pl.col("race_date") < context.race_date),
        regimes=pane.regimes.filter(pl.col("race_date") < context.race_date),
        laps=pane.laps,
        entries=pane.entries,
        race_day_sd=pane.race_day_sd,
        quali_anchor_sd=pane.quali_anchor_sd,
    )
    blind = backtest.forecast(
        truncated, context, context.race_date, np.random.default_rng(SEED), paths=400
    )
    assert blind.outcome.driver_code == whole.outcome.driver_code
    assert blind.outcome.position == whole.outcome.position


def test_the_baselines_are_fitted_on_the_past_only(store, seeded):
    seeded()
    pane = backtest.panel(store)
    entries = pane.results.select(
        "season", "round", "race_date", "driver_code", "grid", "position", "points"
    )
    from pitadvisor.model import baselines

    early = baselines.fit(entries, date(2024, 1, 1), known=pane.entries)
    late = baselines.fit(entries, date(2025, 1, 1), known=pane.entries)
    assert early.lookups["grid"].rows < late.lookups["grid"].rows
