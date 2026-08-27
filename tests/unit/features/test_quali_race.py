from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from pitadvisor.features.clean_pace import DriverPace, SessionPace
from pitadvisor.features.quali_race import (
    NoQualifyingLapError,
    Segment,
    evolution,
    fit_event,
    trend,
)
from pitadvisor.types import SessionKind

Q1_GAIN = 900.0
Q2_GAIN = 400.0
BASE = 90_000.0


def grid(
    pace: dict[str, float],
    q1_gain: float = Q1_GAIN,
    q2_gain: float = Q2_GAIN,
    noise: float = 30.0,
    sandbaggers: tuple[str, ...] = (),
    seed: int = 11,
) -> pl.DataFrame:
    """Fastest fifteen advance to Q2, fastest ten to Q3, exactly as the real format does."""
    rng = np.random.default_rng(seed)
    order = sorted(pace, key=lambda code: pace[code])
    rows = []
    for position, code in enumerate(order):
        held = BASE + pace[code]
        lap = {
            "q1": held + q1_gain + q2_gain + rng.normal(0.0, noise),
            "q2": held + q2_gain + rng.normal(0.0, noise) if position < 15 else None,
            "q3": held + rng.normal(0.0, noise) if position < 10 else None,
        }
        if code in sandbaggers:
            lap["q1"] = (lap["q1"] or 0.0) + 1_200.0
        rows.append(
            {
                "season": 2024,
                "round": 5,
                "race_date": date(2024, 5, 5),
                "driver_code": code,
                "constructor_id": f"team_{position // 2}",
                "q1_millis": lap["q1"],
                "q2_millis": lap["q2"],
                "q3_millis": lap["q3"],
            }
        )
    return pl.DataFrame(rows)


def field(count: int = 20, spread: float = 90.0) -> dict[str, float]:
    return {f"D{index:02d}": index * spread for index in range(count)}


def test_a_known_track_evolution_comes_back():
    steps = {item.segment: item for item in evolution(grid(field()))}
    assert steps[Segment.Q3].offset_millis == 0.0
    assert abs(steps[Segment.Q2].offset_millis - Q2_GAIN) < 60.0
    assert abs(steps[Segment.Q1].offset_millis - (Q1_GAIN + Q2_GAIN)) < 90.0


def test_the_offset_is_monotone_because_the_track_only_rubbers_in():
    steps = {item.segment: item.offset_millis for item in evolution(grid(field()))}
    assert steps[Segment.Q1] > steps[Segment.Q2] > steps[Segment.Q3]


def test_correcting_recovers_the_true_order_a_raw_best_gets_wrong():
    truth = field()
    fitted = fit_event(grid(truth))
    recovered = [driver.driver_code for driver in fitted.drivers]
    assert recovered == sorted(truth, key=lambda code: truth[code])


def test_a_q1_exit_is_not_charged_for_the_greener_track():
    truth = field()
    fitted = fit_event(grid(truth))
    last = fitted.drivers[-1]
    assert last.segment is Segment.Q1
    assert last.evolution_millis > 0.0
    assert last.corrected_millis < last.raw_millis


def test_a_sandbagged_q1_run_does_not_move_the_median_offset():
    truth = field()
    honest = {item.segment: item.offset_millis for item in evolution(grid(truth))}
    faked = {
        item.segment: item.offset_millis
        for item in evolution(grid(truth, sandbaggers=("D00", "D01", "D02", "D03")))
    }
    assert abs(faked[Segment.Q1] - honest[Segment.Q1]) < 60.0


def test_the_spread_is_published_so_the_offset_is_not_read_as_precise():
    steps = {item.segment: item for item in evolution(grid(field(), sandbaggers=("D00", "D01")))}
    assert steps[Segment.Q1].spread_millis > 0.0
    assert steps[Segment.Q1].pairs == 15


def test_too_few_pairs_leaves_the_offset_uncorrected_and_says_so():
    thin = grid(field(count=4))
    steps = {item.segment: item for item in evolution(thin, min_pairs=10)}
    assert steps[Segment.Q1].standard_error_millis == float("inf")
    assert steps[Segment.Q2].offset_millis == 0.0


def test_no_timed_lap_raises_rather_than_returning_an_empty_event():
    empty = grid(field(count=2)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias(f"{segment}_millis") for segment in ("q1", "q2", "q3")
    )
    with pytest.raises(NoQualifyingLapError):
        fit_event(empty)


def session_pace(percent: dict[str, float]) -> SessionPace:
    return SessionPace(
        season=2024,
        round=5,
        session=SessionKind.RACE,
        drivers=[
            DriverPace(
                driver_code=code,
                clean_pace_millis=BASE * (1.0 + value / 100.0),
                standard_error_millis=25.0,
                interval_low_millis=0.0,
                interval_high_millis=0.0,
                clean_laps=20,
                mean_tyre_age=8.0,
                mean_race_progress=25.0,
                percent_off_benchmark=value,
            )
            for code, value in percent.items()
        ],
        b_tyre_millis=50.0,
        b_progress_millis=-30.0,
        compound_offsets_millis={},
        reference_compound="MEDIUM",
        benchmark_millis=BASE,
        clean_laps=400,
        total_laps=1000,
        exclusions={},
        exclusion_rate=0.6,
        condition_number=30.0,
    )


def test_a_car_that_slides_on_sunday_shows_a_positive_delta():
    truth = field(count=20)
    quali = fit_event(grid(truth), session_pace({code: 0.0 for code in truth}))
    slid = {driver.driver_code: driver.delta for driver in quali.drivers}
    assert slid["D00"] is not None
    assert slid["D00"] < 0.0
    assert slid["D19"] is not None
    assert slid["D19"] > 0.0


def test_a_driver_with_no_race_pace_is_listed_rather_than_dropped():
    truth = field(count=20)
    partial = session_pace({code: 0.0 for code in list(truth)[:18]})
    fitted = fit_event(grid(truth), partial)
    assert len(fitted.drivers) == 20
    assert fitted.matched == 18
    assert fitted.unmatched == ["D18", "D19"]
    missing = next(d for d in fitted.drivers if d.driver_code == "D19")
    assert missing.delta is None
    assert missing.interval_low is None


def test_the_delta_interval_carries_both_sessions_errors():
    truth = field(count=20)
    fitted = fit_event(grid(truth), session_pace({code: 0.0 for code in truth}))
    front = next(d for d in fitted.drivers if d.segment is Segment.Q3)
    assert front.delta_standard_error is not None
    assert front.interval_low is not None
    assert front.interval_high is not None
    assert front.interval_low < front.delta < front.interval_high


def events_over(count: int, delta: float) -> list[tuple[date, object]]:
    truth = field(count=20)
    start = date(2024, 3, 3)
    return [
        (
            start + timedelta(days=14 * index),
            fit_event(
                grid(truth, seed=index),
                session_pace({code: -delta for code in truth}),
            ),
        )
        for index in range(count)
    ]


def test_a_standing_saturday_advantage_survives_the_decay():
    stacked = trend(events_over(10, delta=0.5), as_of=date(2025, 1, 1))  # type: ignore[arg-type]
    front = next(d for d in stacked.drivers if d.driver_code == "D00")
    assert front.interval_low <= front.delta <= front.interval_high
    assert front.events == 10
    assert front.effective_events < 10.0


def test_the_trend_refuses_to_see_past_the_as_of_date():
    everything = events_over(10, delta=0.5)
    cut = trend(everything, as_of=date(2024, 4, 1))  # type: ignore[arg-type]
    assert cut.events_used == 3
    assert cut.events_dropped == 7


def test_one_event_gives_no_interval_rather_than_a_fake_tight_one():
    stacked = trend(events_over(1, delta=0.5), as_of=date(2025, 1, 1))  # type: ignore[arg-type]
    assert stacked.drivers[0].standard_error == float("inf")


def test_attempts_are_published_so_the_min_of_three_bias_is_visible():
    fitted = fit_event(grid(field()))
    attempts = {driver.driver_code: driver.attempts for driver in fitted.drivers}
    assert attempts["D00"] == 3
    assert attempts["D12"] == 2
    assert attempts["D19"] == 1
