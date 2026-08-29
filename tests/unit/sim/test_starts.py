from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.sim import starts

AS_OF = date(2025, 1, 1)


def frame(gains: dict[int, int], races: int = 20) -> pl.DataFrame:
    rows = []
    for index in range(races):
        held = AS_OF - timedelta(days=14 * (races - index))
        for slot, gain in gains.items():
            rows.append(
                {
                    "season": 2024,
                    "round": index + 1,
                    "race_date": held,
                    "grid": slot,
                    "start_position": slot - gain,
                }
            )
    return pl.DataFrame(rows)


def test_a_field_that_never_moves_puts_every_bucket_on_holding_station():
    model = starts.fit(frame({slot: 0 for slot in range(1, 21)}), AS_OF)
    assert [round(bucket.mean, 3) for bucket in model.buckets] == [0.0, 0.0, 0.0, 0.0]


def test_the_back_of_the_grid_gains_and_the_front_loses():
    gains = {slot: (2 if slot >= 15 else (-1 if slot <= 3 else 0)) for slot in range(1, 21)}
    model = starts.fit(frame(gains), AS_OF)
    assert model.buckets[0].mean < -0.5
    assert model.buckets[3].mean > 1.5


def test_a_race_after_the_cutoff_is_not_in_the_fit():
    rows = frame({slot: 0 for slot in range(1, 21)})
    ahead = rows.with_columns(
        pl.lit(AS_OF + timedelta(days=7)).alias("race_date"),
        (pl.col("grid") - 5).alias("start_position"),
    )
    model = starts.fit(pl.concat([rows, ahead]), AS_OF)
    assert model.events_dropped == ahead.height
    assert all(abs(bucket.mean) < 1e-9 for bucket in model.buckets)


def test_an_empty_history_is_refused_rather_than_guessed():
    with pytest.raises(starts.NoStartsError):
        starts.fit(frame({1: 0}).filter(pl.col("grid") > 99), AS_OF)


def test_every_path_comes_back_a_permutation():
    model = starts.fit(frame({slot: 0 for slot in range(1, 21)}), AS_OF)
    grid = np.arange(1, 21)
    drawn = starts.sample(model, grid, np.random.default_rng(4), paths=200)
    assert drawn.shape == (200, 20)
    for row in drawn:
        assert sorted(row.tolist()) == list(range(1, 21))


def test_the_sampled_order_tracks_the_gains_it_was_fitted_on():
    gains = {slot: (3 if slot >= 15 else 0) for slot in range(1, 21)}
    model = starts.fit(frame(gains), AS_OF)
    drawn = starts.sample(model, np.arange(1, 21), np.random.default_rng(9), paths=500)
    # the back six were all fitted gaining three places and cannot all have them, because a
    # running order is a permutation. what they can do is move up, and they do
    assert drawn[:, 14:].mean() < 17.5
    assert drawn[:, :3].mean() > 2.0
