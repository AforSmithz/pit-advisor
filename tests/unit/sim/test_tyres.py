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


def plans(rows: list[tuple[str, int, str]], when: date = date(2024, 6, 1)) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": 2024,
                "round": 1,
                "race_date": when,
                "circuit_id": circuit,
                "driver_code": f"D{index}",
                "grid": grid,
                "sequence": sequence,
            }
            for index, (circuit, grid, sequence) in enumerate(rows)
        ]
    )


def compound_model(**overrides) -> tyres.TyreModel:
    base = tyres.fit(stops(), degradation(), "monza", AS_OF)
    return base.model_copy(
        update={
            "strategies": [
                tyres.Strategy(sequence="MH", front=1.0, back=0.0),
                tyres.Strategy(sequence="SMH", front=0.0, back=1.0),
            ],
            "stint_weights": {"SOFT": 12.0, "MEDIUM": 18.0, "HARD": 26.0},
            "stop_spread": 0.02,
            **overrides,
        }
    )


def test_a_one_compound_or_wet_race_is_not_a_dry_plan():
    frame = plans([("monza", 1, "MH"), ("monza", 2, "M"), ("monza", 3, "MI"), ("monza", 4, "HHH")])
    found = {item.sequence for item in tyres.fit_strategies(frame, "monza", AS_OF)}
    assert found == {"MH"}


def test_the_front_and_the_back_of_the_grid_keep_their_own_mix():
    frame = plans([("monza", 1, "MH")] * 30 + [("monza", 15, "HM")] * 30)
    found = {item.sequence: item for item in tyres.fit_strategies(frame, "monza", AS_OF)}
    assert found["MH"].front > 0.9
    assert found["HM"].back > 0.9
    assert sum(item.front for item in found.values()) == pytest.approx(1.0)


def test_a_circuit_with_little_history_leans_on_the_field():
    frame = plans([("bahrain", 3, "SHH")] * 200 + [("monza", 3, "MH")] * 2)
    found = {item.sequence: item for item in tyres.fit_strategies(frame, "monza", AS_OF)}
    assert found["SHH"].front > found["MH"].front


def test_a_plan_stops_once_less_than_it_has_compounds_and_runs_them_in_order():
    model = compound_model()
    grid = np.array([1, 2, 15, 16])
    pitting, plan = tyres.sample_plan(model, grid, 200, 4, 50, np.random.default_rng(1))
    stops_made = pitting.sum(axis=2)
    assert (stops_made[:, :2] == 1).all()
    assert (stops_made[:, 2:] == 2).all()
    assert (plan[:, 0, :2] == [tyres.LETTERS["M"], tyres.LETTERS["H"]]).all()
    assert (plan[:, 2, :3] == [tyres.LETTERS[x] for x in "SMH"]).all()


def test_a_short_soft_stint_comes_in_earlier_than_a_long_hard_one():
    model = compound_model(
        strategies=[
            tyres.Strategy(sequence="SH", front=1.0, back=1.0),
        ]
    )
    pitting, _ = tyres.sample_plan(model, np.array([1]), 300, 1, 60, np.random.default_rng(2))
    first = pitting[:, 0, :].argmax(axis=1)
    assert np.median(first) < 30


def test_a_safety_car_pulls_a_stop_that_was_due_forward():
    pitting = np.zeros((2, 2, 30), dtype=bool)
    pitting[:, 0, 14] = True
    pitting[:, 1, 25] = True
    under = np.array([True, False])
    moved = tyres.pit_under_caution(pitting.copy(), under, 10)
    assert moved[0, 0, 10]
    assert not moved[0, 0, 14]
    assert moved[0, 1, 25]
    assert not moved[0, 1, 10]
    assert moved[1, 0, 14]
    assert not moved[1, 0, 10]


def test_softs_wear_faster_than_hards_once_the_field_has_run_both():
    rows = [
        {
            "season": 2024,
            "round": 1,
            "race_date": date(2024, 6, 1),
            "circuit_id": "monza",
            "driver_code": f"D{i}",
            "compound": compound,
            "laps": 20,
            "wear_millis": wear,
        }
        for i in range(10)
        for compound, wear in (("SOFT", 90.0), ("MEDIUM", 60.0), ("HARD", 40.0))
    ]
    ratio = tyres._wear_ratio(pl.DataFrame(rows), AS_OF, tyres.HALF_LIFE_EVENTS)
    assert ratio["SOFT"] > ratio["MEDIUM"] > ratio["HARD"]
