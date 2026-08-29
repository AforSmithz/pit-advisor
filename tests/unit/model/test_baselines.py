from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.model import baselines

START = date(2024, 3, 1)
CODES = [f"D{index:02d}" for index in range(20)]
POINTS = {1: 25.0, 2: 18.0, 3: 15.0, 4: 12.0, 5: 10.0}


def lake(races: int = 20, shuffle: bool = False, seed: int = 0) -> pl.DataFrame:
    """A championship where the grid decides everything, unless shuffle scrambles the result
    away from it, which is the case a lookup should come back knowing nothing about."""
    rng = np.random.default_rng(seed)
    rows = []
    for round_ in range(1, races + 1):
        finishing = rng.permutation(len(CODES)) + 1 if shuffle else np.arange(1, len(CODES) + 1)
        for slot, code in enumerate(CODES, start=1):
            place = int(finishing[slot - 1])
            rows.append(
                {
                    "season": 2024,
                    "round": round_,
                    "race_date": START + timedelta(days=14 * round_),
                    "driver_code": code,
                    "grid": slot,
                    "position": place,
                    "points": POINTS.get(place, 0.0),
                }
            )
    return pl.DataFrame(rows)


def test_a_pit_lane_start_is_the_back_of_the_field_not_the_front():
    rows = lake(races=2).with_columns(
        pl.when(pl.col("grid") == 1).then(0).otherwise(pl.col("grid")).alias("grid")
    )
    field = baselines.entries(rows, 2024, 2)
    assert max(field.grid) == baselines.FIELD
    assert 0 not in field.grid


def test_a_driver_the_history_has_never_seen_has_no_rank():
    field = baselines.entries(lake(races=1), 2024, 1)
    assert set(field.standings) == {None}
    assert set(field.last_race) == {None}


def test_standings_fall_back_to_last_season_in_round_one():
    last = lake(races=5)
    this = lake(races=1).with_columns(
        pl.lit(2025, dtype=pl.Int64).alias("season"),
        pl.lit(START + timedelta(days=400)).alias("race_date"),
    )
    field = baselines.entries(pl.concat([last, this]), 2025, 1)
    ranked = dict(zip(field.driver_code, field.standings, strict=True))
    assert ranked[CODES[0]] == 1
    assert ranked[CODES[19]] is not None


def test_a_perfectly_predictable_grid_gives_a_confident_lookup():
    fitted = baselines.fit(lake(), START + timedelta(days=400))
    table = np.asarray(fitted.lookups["grid"].table)
    # neighbouring slots pool, so even a grid that decides everything does not read 1.0
    assert table[0, 0] > 0.4
    assert table.argmax(axis=1).tolist() == list(range(baselines.FIELD))


def test_a_random_grid_gives_a_flat_lookup():
    fitted = baselines.fit(lake(shuffle=True, seed=4), START + timedelta(days=400))
    table = np.asarray(fitted.lookups["grid"].table)
    assert table.max() < 0.2


def test_every_row_of_every_lookup_is_a_distribution():
    fitted = baselines.fit(lake(), START + timedelta(days=400))
    for lookup in fitted.lookups.values():
        table = np.asarray(lookup.table)
        assert np.allclose(table.sum(axis=1), 1.0)
        assert (table > 0).all()


def test_a_race_after_the_cutoff_is_not_in_the_lookup():
    rows = lake()
    early = baselines.fit(rows, START + timedelta(days=40))
    late = baselines.fit(rows, START + timedelta(days=400))
    assert early.lookups["grid"].rows < late.lookups["grid"].rows


def test_precomputed_entries_give_the_same_fit_as_building_them_again():
    rows = lake()
    cutoff = START + timedelta(days=400)
    known = baselines.all_entries(rows)
    assert np.allclose(
        np.asarray(baselines.fit(rows, cutoff, known=known).lookups["grid"].table),
        np.asarray(baselines.fit(rows, cutoff).lookups["grid"].table),
    )


def test_predicting_an_unknown_race_is_refused():
    with pytest.raises(baselines.NoHistoryError):
        baselines.entries(lake(), 2024, 99)


def test_a_missing_rank_predicts_as_the_back_of_the_field():
    fitted = baselines.fit(lake(), START + timedelta(days=400))
    lookup = fitted.lookups["grid"]
    assert np.allclose(lookup.predict([None]), lookup.predict([baselines.FIELD]))
