from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.sim import tyres

AS_OF = date(2025, 1, 1)


def stops(circuit: str = "monza", loss: float = 22_000.0, per_driver: int = 1) -> pl.DataFrame:
    rows = []
    for round_ in range(1, 9):
        for driver in range(20):
            for stop in range(1, per_driver + 1):
                rows.append(
                    {
                        "season": 2024,
                        "round": round_,
                        "race_date": AS_OF - timedelta(days=14 * (10 - round_)),
                        "circuit_id": circuit,
                        "driver_code": f"D{driver:02d}",
                        "stop": stop,
                        "fraction": stop / (per_driver + 1),
                        "excess_millis": loss,
                        "starters": 20,
                    }
                )
    return pl.DataFrame(rows)


def degradation(circuit: str = "monza", millis: float = 60.0) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": 2024,
                "round": round_,
                "race_date": AS_OF - timedelta(days=14 * (10 - round_)),
                "circuit_id": circuit,
                "millis_per_lap": millis,
            }
            for round_ in range(1, 9)
        ]
    )


def test_the_fit_recovers_the_numbers_it_was_generated_from():
    model = tyres.fit(stops(), degradation(), "monza", AS_OF)
    assert model.pit_loss_millis == pytest.approx(22_000.0)
    assert model.degradation_millis == pytest.approx(60.0)
    assert model.stop_counts[1] > 0.9


def test_a_red_flag_stop_does_not_drag_the_pit_loss_with_it():
    """The mean of this is thirty-two seconds and the middle of it is twenty-two."""
    clean = stops()
    freak = clean.head(20).with_columns(pl.lit(200_000.0).alias("excess_millis"))
    model = tyres.fit(pl.concat([clean, freak]), degradation(), "monza", AS_OF)
    assert 21_000.0 < model.pit_loss_millis < 24_000.0


def test_a_two_stop_field_reads_as_a_two_stop_field():
    model = tyres.fit(stops(per_driver=2), degradation(), "monza", AS_OF)
    assert model.stop_counts[2] > model.stop_counts[1]


def test_a_car_that_never_pitted_is_counted_rather_than_missed():
    half = stops().filter(pl.col("driver_code") < "D10")
    model = tyres.fit(half, degradation(), "monza", AS_OF)
    assert model.stop_counts[0] > 0.3


def test_an_unseen_circuit_borrows_the_field():
    model = tyres.fit(stops("monza"), degradation("monza"), "elsewhere", AS_OF)
    assert model.pit_loss_millis == model.field_pit_loss_millis
    assert model.weighted_events == 0.0


def test_nothing_after_the_cutoff_is_fitted():
    later = stops().with_columns(
        pl.lit(AS_OF + timedelta(days=1)).alias("race_date"),
        pl.lit(90_000.0).alias("excess_millis"),
    )
    model = tyres.fit(pl.concat([stops(), later]), degradation(), "monza", AS_OF)
    assert model.pit_loss_millis == pytest.approx(22_000.0)


def test_a_sampled_plan_stops_the_number_of_times_it_planned_to():
    model = tyres.fit(stops(per_driver=2), degradation(), "monza", AS_OF)
    drawn = tyres.sample_stops(model, paths=500, drivers=20, laps=60, rng=np.random.default_rng(6))
    assert drawn.shape == (500, 20, 60)
    per_car = drawn.sum(axis=2)
    assert per_car.max() <= tyres.MAX_STOPS
    assert 1.5 < per_car.mean() < 2.5


def test_nobody_pits_on_the_last_lap():
    model = tyres.fit(stops(), degradation(), "monza", AS_OF)
    drawn = tyres.sample_stops(model, paths=200, drivers=20, laps=60, rng=np.random.default_rng(1))
    assert not drawn[:, :, -1].any()


def test_an_empty_history_still_produces_a_plan():
    empty = stops().filter(pl.col("stop") > 99)
    model = tyres.fit(empty, degradation().filter(pl.col("season") > 9999), "monza", AS_OF)
    drawn = tyres.sample_stops(model, paths=50, drivers=20, laps=50, rng=np.random.default_rng(0))
    assert drawn.sum() > 0
