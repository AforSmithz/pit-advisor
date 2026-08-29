import numpy as np
import pytest

from pitadvisor.features.assemble import next_event
from pitadvisor.model import backtest, calibrate

SEED = 5


@pytest.fixture
def report(store, seeded):
    seeded()
    pane = backtest.panel(store)
    return backtest.run(
        pane, 2024, 4, np.random.default_rng(SEED), "test-run", paths=200, seed=SEED
    )


def test_the_summary_names_every_model_and_what_it_scored(report):
    text = calibrate.summarise(report)
    for scored in report.scored:
        assert scored.name in text
    assert "log loss" in text
    assert "race" in text


def test_the_summary_says_which_baselines_were_beaten(report):
    text = calibrate.summarise(report)
    assert "beats" in text
    for item in report.paired:
        assert f"vs {item.baseline}" in text


def test_the_summary_carries_the_assumptions_the_simulation_ran_under(report):
    text = calibrate.summarise(report)
    for item in report.assumptions:
        assert item.name in text


def test_writing_lands_the_three_artifacts_the_phase_asks_for(report, tmp_path):
    written = calibrate.write(report, tmp_path)
    assert {path.name for path in written} == {
        calibrate.REPORT,
        calibrate.SUMMARY,
        calibrate.FIGURE,
    }
    assert all(path.exists() and path.stat().st_size > 0 for path in written)


def test_the_report_round_trips_through_its_own_json(report, tmp_path):
    calibrate.write(report, tmp_path)
    again = backtest.Report.model_validate_json((tmp_path / calibrate.REPORT).read_text())
    assert again.scored[0].log_loss.value == report.scored[0].log_loss.value
    assert again.beats_baselines == report.beats_baselines


def test_the_plot_is_a_png_and_not_an_empty_one(report, tmp_path):
    path = calibrate.render(report, tmp_path)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert path.stat().st_size > 5_000


def test_a_forecast_and_the_report_agree_on_the_field(store, seeded):
    seeded()
    pane = backtest.panel(store)
    context = next_event(store, __import__("datetime").date(2024, 1, 1))
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(SEED), paths=100
    )
    assert len(predicted.outcome.driver_code) <= backtest.FIELD
