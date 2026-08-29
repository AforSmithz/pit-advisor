import numpy as np
import pytest

from pitadvisor.features.assemble import assemble, event_at
from pitadvisor.ingest.raw_store import LocalObjectStore
from pitadvisor.model import backtest
from pitadvisor.outputs.view_contracts import (
    calibration_view,
    driver_view,
    emit,
    evidence_from,
    forecast_view,
    track_view,
    weekend_view,
)

SEASON = 2024
ROUND = 6


@pytest.fixture(scope="module")
def views(tmp_path_factory, seed_lake):
    store = LocalObjectStore(tmp_path_factory.mktemp("lake"))
    seed_lake(store)
    context = event_at(store, SEASON, ROUND)
    assembled = assemble(store, context, "run-1")
    for view in (weekend_view(assembled), driver_view(assembled), track_view(assembled)):
        emit(store, view)
    pane = backtest.panel(store)
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(5), paths=200
    )
    seats, _ = backtest.seats_for(pane, context, context.race_date)
    grid = backtest.grid_for(pane, context, predicted.outcome.driver_code)
    report = backtest.run(pane, SEASON, 3, np.random.default_rng(5), "run-1", paths=120, seed=5)
    emit(store, forecast_view(predicted, context, "run-1", seats, grid, evidence_from(report)))
    emit(store, calibration_view(report))
    return store
