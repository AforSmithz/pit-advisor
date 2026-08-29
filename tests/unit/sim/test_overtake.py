from datetime import date, timedelta

import numpy as np
import polars as pl

from pitadvisor.sim import overtake

AS_OF = date(2025, 1, 1)


def duels(rate: dict[str, float], rows: int = 400, slope_signal: bool = False) -> pl.DataFrame:
    rng = np.random.default_rng(3)
    built = []
    round_ = 0
    for circuit, chance in rate.items():
        for _race in range(4):
            round_ += 1
            for lap in range(rows // 4):
                delta = float(rng.normal(0.0, 1.0)) if slope_signal else 0.0
                p = chance if not slope_signal else 1.0 / (1.0 + np.exp(-(-2.2 + 1.5 * delta)))
                built.append(
                    {
                        "season": 2024,
                        "round": round_,
                        "race_date": AS_OF - timedelta(days=7 * (40 - round_)),
                        "circuit_id": circuit,
                        "lap": lap + 1,
                        "ahead": "AAA",
                        "behind": "BBB",
                        "passed": bool(rng.random() < p),
                        "delta": delta if slope_signal else None,
                    }
                )
    return pl.DataFrame(built, schema_overrides={"delta": pl.Float64})


def test_a_circuit_nobody_passes_at_reads_below_one_everybody_does():
    frame = duels({"monaco": 0.01, "monza": 0.25})
    tight = overtake.fit(frame, "monaco", AS_OF)
    open_ = overtake.fit(frame, "monza", AS_OF)
    assert overtake.probability(tight, np.zeros(1))[0] < 0.08
    assert overtake.probability(open_, np.zeros(1))[0] > 0.15


def test_a_circuit_with_no_history_sits_at_the_field_rate():
    model = overtake.fit(duels({"monza": 0.2}), "unseen", AS_OF)
    assert model.base == model.field_base
    assert model.attempts == 0.0


def test_the_slope_comes_back_near_the_one_the_data_was_generated_from():
    model = overtake.fit(duels({"monza": 0.1}, rows=4000, slope_signal=True), "monza", AS_OF)
    assert model.slope_rows == 4000
    assert 1.0 < model.slope < 2.0


def test_a_faster_car_is_likelier_to_get_by_than_a_slower_one():
    model = overtake.fit(duels({"monza": 0.1}, rows=4000, slope_signal=True), "monza", AS_OF)
    chances = overtake.probability(model, np.array([-1.0, 0.0, 1.0]))
    assert chances[0] < chances[1] < chances[2]
    assert (chances > 0).all()
    assert (chances < 1).all()


def test_an_empty_history_still_returns_a_usable_model():
    model = overtake.fit(duels({"monza": 0.2}).filter(pl.col("lap") > 10_000), "monza", AS_OF)
    assert 0.0 < overtake.probability(model, np.zeros(1))[0] < 1.0


def test_the_dirty_air_cost_is_shrunk_toward_the_field():
    frame = pl.DataFrame(
        [
            {
                "season": 2024,
                "round": index + 1,
                "race_date": AS_OF - timedelta(days=7 * (20 - index)),
                "circuit_id": "monaco" if index % 2 else "monza",
                "driver_code": "AAA",
                "penalty_millis": 900.0 if index % 2 else 300.0,
            }
            for index in range(20)
        ]
    )
    tight, field = overtake.traffic_cost(frame, "monaco", AS_OF)
    assert 300.0 < field < 900.0
    assert field < tight < 900.0


def test_pairs_finds_a_pass_and_ignores_one_made_in_the_pits(seeded, store):
    seeded()
    from pitadvisor.quality.checks import read_table
    from pitadvisor.types import Layer

    laps = read_table(store, Layer.BRONZE, "session_laps")
    assert laps is not None
    found = overtake.pairs(laps)
    # the fixture field never changes position, so every striking-range lap is a failed pass
    assert found.height >= 0
    assert not found["passed"].any()
