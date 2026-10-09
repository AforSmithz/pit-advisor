import numpy as np

from pitadvisor.model import pool
from pitadvisor.model.metrics import log_loss

PLACES = 8


def spread(sharpness: float, size: int = PLACES) -> np.ndarray:
    # driver i is most likely to finish i-th, how likely is the sharpness
    axis = np.arange(size)
    logits = -sharpness * np.abs(axis[:, None] - axis[None, :])
    grid = np.exp(logits)
    return grid / grid.sum(axis=1, keepdims=True)


def races(count: int, truth: float, told: float, seed: int = 3) -> list[pool.Scored]:
    rng = np.random.default_rng(seed)
    real = spread(truth)
    out = []
    for _ in range(count):
        actual = np.array([rng.choice(PLACES, p=row) for row in real])
        out.append(pool.Scored(sim=spread(told), grid=spread(told), actual=actual, starters=PLACES))
    return out


def test_balance_leaves_every_row_and_every_place_whole():
    rng = np.random.default_rng(1)
    raw = rng.random((PLACES, PLACES))
    raw /= raw.sum(axis=1, keepdims=True)
    out = pool.balance(raw, PLACES)
    assert np.allclose(out.sum(axis=1), 1.0)
    assert np.allclose(out.sum(axis=0), 1.0, atol=1e-6)


def test_balance_with_fewer_rows_than_places_shares_each_place():
    rng = np.random.default_rng(2)
    raw = rng.random((6, PLACES))
    raw /= raw.sum(axis=1, keepdims=True)
    out = pool.balance(raw, PLACES)
    assert np.allclose(out.sum(axis=1), 1.0)
    assert np.allclose(out.sum(axis=0), 6 / PLACES, atol=1e-6)


def test_the_identity_pool_changes_nothing_a_balanced_forecast_already_says():
    sim = pool.balance(spread(0.7), PLACES)
    out = pool.combine(sim, None, PLACES, pool.IDENTITY)
    assert np.allclose(out, sim, atol=1e-6)


def test_places_past_the_field_get_nothing():
    sim = np.hstack([spread(0.7, 6), np.zeros((6, 2))])
    out = pool.combine(sim, None, 6, pool.Pool(sim_power=1.4, grid_power=0.0, races=1))
    assert np.allclose(out[:, 6:], 0.0)
    assert np.allclose(out.sum(axis=1), 1.0)


def test_a_simulation_that_spreads_the_field_too_wide_gets_sharpened():
    fitted = pool.fit(races(60, truth=1.2, told=0.6))
    assert fitted.sim_power + fitted.grid_power > 1.2


def test_a_simulation_that_is_already_right_is_left_near_alone():
    fitted = pool.fit(races(80, truth=0.8, told=0.8))
    assert 0.8 <= fitted.sim_power + fitted.grid_power <= 1.3


def test_the_fitted_pool_scores_no_worse_than_the_raw_simulation_on_its_own_history():
    history = races(40, truth=1.2, told=0.6)
    fitted = pool.fit(history)

    def mean_loss(chosen: pool.Pool) -> float:
        return float(
            np.mean(
                [
                    log_loss(pool.combine(r.sim, r.grid, r.starters, chosen), r.actual)
                    for r in history
                ]
            )
        )

    assert mean_loss(fitted) <= mean_loss(pool.IDENTITY)


def test_no_history_is_the_identity():
    assert pool.fit([]) == pool.IDENTITY


def test_without_a_grid_the_grid_power_stays_zero():
    fitted = pool.fit(races(30, truth=1.2, told=0.6), with_grid=False)
    assert fitted.grid_power == 0.0
    assert fitted.races == 30
