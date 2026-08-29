from datetime import date, timedelta

import numpy as np
import polars as pl

from pitadvisor.sim import safety_car

AS_OF = date(2025, 1, 1)
LAPS = 60


def frame(per_circuit: dict[str, list[list[int]]]) -> pl.DataFrame:
    rows = []
    round_ = 0
    for circuit, races in per_circuit.items():
        for flagged in races:
            round_ += 1
            for lap in range(1, LAPS + 1):
                rows.append(
                    {
                        "season": 2024,
                        "round": round_,
                        "race_date": AS_OF - timedelta(days=14 * (60 - round_)),
                        "circuit_id": circuit,
                        "lap": lap,
                        "safety_car": lap in flagged,
                    }
                )
    return pl.DataFrame(rows)


def test_a_run_of_flagged_laps_is_one_deployment_not_five():
    flag = np.array([False, True, True, True, True, False, True, False])
    assert safety_car._periods(flag) == (2, 5.0)


def test_a_circuit_that_never_needs_one_reads_below_the_field():
    rows = frame({"clean": [[] for _ in range(6)], "messy": [list(range(5, 15)) for _ in range(6)]})
    calm = safety_car.fit(rows, "clean", AS_OF)
    rough = safety_car.fit(rows, "messy", AS_OF)
    assert calm.per_lap < calm.field_per_lap < rough.per_lap


def test_a_circuit_with_no_history_falls_back_to_the_field_rate():
    rows = frame({"known": [list(range(5, 10)) for _ in range(6)]})
    model = safety_car.fit(rows, "unseen", AS_OF)
    assert model.per_lap == model.field_per_lap
    assert model.weighted_races == 0.0


def test_nothing_after_the_cutoff_reaches_the_fit():
    rows = frame({"known": [[] for _ in range(4)]})
    later = rows.with_columns(
        pl.lit(AS_OF + timedelta(days=1)).alias("race_date"), pl.lit(True).alias("safety_car")
    )
    model = safety_car.fit(pl.concat([rows, later]), "known", AS_OF)
    assert model.field_per_lap == 0.0


def test_a_sampled_period_lasts_more_than_the_lap_it_started_on():
    rows = frame({"messy": [list(range(10, 18)) for _ in range(8)]})
    model = safety_car.fit(rows, "messy", AS_OF)
    drawn = safety_car.sample(model, LAPS, 400, np.random.default_rng(2))
    assert drawn.shape == (400, LAPS)
    lengths = [int(row.sum()) for row in drawn if row.any()]
    assert lengths
    assert float(np.mean(lengths)) > 1.5


def test_a_zero_rate_never_calls_one():
    rows = frame({"clean": [[] for _ in range(10)]})
    model = safety_car.fit(rows, "clean", AS_OF)
    drawn = safety_car.sample(model, LAPS, 200, np.random.default_rng(1))
    assert not drawn.any()
