import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pitadvisor.model.metrics import (
    Bin,
    MalformedForecastError,
    accuracy,
    binary_brier,
    brier,
    calibration_error,
    log_loss,
    race_bootstrap,
    reliability,
)


def simplex(rows: int, classes: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    drawn = rng.dirichlet(np.ones(classes), size=rows)
    return drawn


def test_a_perfect_forecast_scores_zero_on_both():
    grid = np.eye(4)
    truth = np.arange(4)
    assert log_loss(grid, truth) == pytest.approx(0.0)
    assert brier(grid, truth) == pytest.approx(0.0)
    assert accuracy(grid, truth) == pytest.approx(1.0)


def test_the_uniform_forecast_scores_log_of_the_field():
    classes = 20
    grid = np.full((5, classes), 1 / classes)
    assert log_loss(grid, np.zeros(5, dtype=int)) == pytest.approx(np.log(classes))


def test_a_certain_and_wrong_forecast_is_finite_rather_than_infinite():
    grid = np.array([[1.0, 0.0]])
    assert np.isfinite(log_loss(grid, np.array([1])))
    assert brier(grid, np.array([1])) == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("grid", "truth", "detail"),
    [
        (np.array([[0.5, 0.4]]), np.array([0]), "sum"),
        (np.array([[1.5, -0.5]]), np.array([0]), "negative"),
        (np.array([[0.5, 0.5]]), np.array([0, 1]), "outcomes"),
        (np.array([[0.5, 0.5]]), np.array([7]), "class set"),
        (np.array([0.5, 0.5]), np.array([0]), "rows, classes"),
    ],
)
def test_a_malformed_forecast_is_refused(grid, truth, detail):
    with pytest.raises(MalformedForecastError, match=detail):
        log_loss(grid, truth)


@given(
    weight=st.lists(st.floats(0.01, 10.0), min_size=2, max_size=8),
    outcome=st.integers(0, 7),
)
@settings(max_examples=200, deadline=None)
def test_log_loss_is_never_negative(weight, outcome):
    grid = np.array(weight) / sum(weight)
    truth = min(outcome, len(weight) - 1)
    assert log_loss(grid.reshape(1, -1), np.array([truth])) >= 0.0
    assert 0.0 <= brier(grid.reshape(1, -1), np.array([truth])) <= 2.0


@given(
    hit=st.floats(0.02, 0.98),
    shift=st.floats(0.01, 0.5),
    classes=st.integers(2, 12),
)
@settings(max_examples=200, deadline=None)
def test_moving_probability_off_the_truth_can_only_cost(hit, shift, classes):
    """Confidence in the wrong place is monotone: the same forecast with less mass on what
    happened scores worse, whatever it does with the rest."""
    lower = max(hit - shift, 1e-4)

    def spread(mass: float) -> np.ndarray:
        rest = (1.0 - mass) / (classes - 1)
        row = np.full(classes, rest)
        row[0] = mass
        return row.reshape(1, -1)

    truth = np.array([0])
    assert log_loss(spread(lower), truth) >= log_loss(spread(hit), truth)
    assert binary_brier(np.array([lower]), np.array([1.0])) >= binary_brier(
        np.array([hit]), np.array([1.0])
    )


@given(hit=st.floats(0.5, 0.999999))
@settings(max_examples=100, deadline=None)
def test_log_loss_goes_to_zero_as_the_forecast_goes_to_certainty(hit):
    grid = np.array([[hit, 1.0 - hit]])
    assert log_loss(grid, np.array([0])) <= -np.log(0.5) + 1e-9
    assert log_loss(grid, np.array([0])) == pytest.approx(-np.log(hit))


def test_reliability_bins_a_forecast_that_always_happens():
    p = np.array([0.05, 0.15, 0.95, 0.85])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    curve = reliability(p, y, bins=10)
    assert [item.count for item in curve] == [1, 1, 1, 1]
    assert calibration_error(curve) == pytest.approx(0.1, abs=0.01)


def test_a_forecast_that_means_what_it_says_has_almost_no_calibration_error():
    rng = np.random.default_rng(11)
    p = rng.uniform(0.0, 1.0, 20_000)
    y = (rng.uniform(0.0, 1.0, 20_000) < p).astype(float)
    assert calibration_error(reliability(p, y)) < 0.02


def test_probability_one_lands_in_the_top_bin_not_off_the_end():
    curve = reliability(np.array([1.0, 0.0]), np.array([1.0, 0.0]), bins=10)
    assert [(item.low, item.high) for item in curve] == [(0.0, 0.1), (0.9, 1.0)]


def test_calibration_error_of_an_empty_curve_is_not_a_number():
    assert np.isnan(calibration_error([]))
    assert np.isnan(calibration_error([Bin(low=0, high=1, forecast=0, observed=0, count=0)]))


def test_resampling_races_gives_a_wider_interval_than_resampling_drivers():
    """The whole point of §4.4. Drivers inside a race are correlated, so treating them as
    independent draws reports an interval the evidence does not support."""
    rng = np.random.default_rng(3)
    races, cars = 40, 20
    # one shared shock per race is exactly the correlation the race-level resample respects
    shock = rng.normal(0.0, 0.8, races)
    value = np.repeat(shock, cars) + rng.normal(0.0, 0.2, races * cars)
    race = np.repeat(np.arange(races), cars)

    def mean(rows: np.ndarray) -> float:
        return float(value[rows].mean())

    by_race = race_bootstrap(race, mean, np.random.default_rng(1), draws=400)
    by_driver = race_bootstrap(np.arange(races * cars), mean, np.random.default_rng(1), draws=400)
    assert (by_race.high - by_race.low) > 3 * (by_driver.high - by_driver.low)
    assert by_race.low < by_race.value < by_race.high


def test_one_race_cannot_be_resampled():
    interval = race_bootstrap(np.zeros(5), lambda rows: float(len(rows)), np.random.default_rng(0))
    assert interval.draws == 0
    assert np.isnan(interval.low)


def test_an_empty_scoring_set_is_not_a_number_rather_than_a_crash():
    assert np.isnan(log_loss(np.zeros((0, 5)), np.zeros(0, dtype=int)))
    assert np.isnan(brier(np.zeros((0, 5)), np.zeros(0, dtype=int)))
    assert np.isnan(binary_brier(np.zeros(0), np.zeros(0)))
