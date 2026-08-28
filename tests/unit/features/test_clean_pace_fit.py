import numpy as np
import polars as pl
import pytest

from pitadvisor.features.clean_pace import (
    REFERENCE_LAPS_REMAINING,
    REFERENCE_TYRE_AGE,
    UnidentifiableFitError,
    fit_session,
)

B_TYRE = 80.0
B_PROGRESS = 50.0
OFFSETS = {"SOFT": -300.0, "MEDIUM": 0.0, "HARD": 200.0}
DRIVERS = {"AAA": 0.0, "BBB": 400.0, "CCC": 800.0, "DDD": 1200.0, "EEE": 1600.0, "FFF": 2000.0}
BASE = 90_000.0
# two of the three plans put medium in the middle, so medium is the reference and the
# stint-to-compound map is not injective, which is what keeps the design identified
PLANS = [
    ["MEDIUM", "HARD", "MEDIUM"],
    ["SOFT", "MEDIUM", "HARD"],
    ["MEDIUM", "SOFT", "MEDIUM"],
]
TOTAL_LAPS = 45
STINT_LENGTH = 15


def session(noise_millis: float = 120.0, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for position, (code, effect) in enumerate(DRIVERS.items(), start=1):
        plan = PLANS[(position - 1) % len(PLANS)]
        for lap in range(1, TOTAL_LAPS + 1):
            stint = (lap - 1) // STINT_LENGTH
            lap_in_stint = (lap - 1) % STINT_LENGTH + 1
            compound = plan[stint]
            remaining = TOTAL_LAPS - lap
            rows.append(
                {
                    "season": 2024,
                    "round": 5,
                    "session": "race",
                    "driver_code": code,
                    "lap": lap,
                    "lap_time_millis": int(
                        BASE
                        + effect
                        + B_TYRE * lap_in_stint
                        + B_PROGRESS * remaining
                        + OFFSETS[compound]
                        + rng.normal(0, noise_millis)
                    ),
                    "stint": stint + 1,
                    "lap_in_stint": lap_in_stint,
                    "compound": compound,
                    "is_deleted": False,
                    "is_accurate": True,
                    "track_status": "1",
                    "pit_in": lap_in_stint == STINT_LENGTH and lap != TOTAL_LAPS,
                    "pit_out": lap_in_stint == 1 and lap != 1,
                    "position": position,
                }
            )
    return pl.DataFrame(rows)


def test_the_fit_recovers_the_coefficients_it_was_generated_from():
    fit = fit_session(session())
    assert fit is not None
    assert fit.b_tyre_millis == pytest.approx(B_TYRE, abs=12)
    assert fit.b_progress_millis == pytest.approx(B_PROGRESS, abs=12)
    assert fit.reference_compound == "MEDIUM"
    for compound, truth in OFFSETS.items():
        if compound == "MEDIUM":
            continue
        assert fit.compound_offsets_millis[compound] == pytest.approx(truth, abs=60)


def test_the_driver_effects_come_back_in_the_right_order_and_spacing():
    fit = fit_session(session())
    assert fit is not None
    paced = {d.driver_code: d.clean_pace_millis for d in fit.drivers}
    fastest = paced["AAA"]
    for code, effect in DRIVERS.items():
        assert paced[code] - fastest == pytest.approx(effect, abs=60)


def test_the_truth_lands_inside_the_reported_interval():
    fit = fit_session(session())
    assert fit is not None
    for driver in fit.drivers:
        truth = (
            BASE
            + DRIVERS[driver.driver_code]
            + B_TYRE * REFERENCE_TYRE_AGE
            + B_PROGRESS * REFERENCE_LAPS_REMAINING
        )
        assert driver.interval_low_millis <= truth <= driver.interval_high_millis


def test_a_noisier_session_reports_wider_intervals():
    def width(fit):
        return np.mean([d.interval_high_millis - d.interval_low_millis for d in fit.drivers])

    tight = fit_session(session(noise_millis=80))
    loose = fit_session(session(noise_millis=600))
    assert tight is not None
    assert loose is not None
    assert width(loose) > 3 * width(tight)


def test_a_confounded_design_refuses_rather_than_pseudo_inverting():
    # every driver on one compound for the whole race: the compound dummy is the intercept
    frame = session().with_columns(
        pl.when(pl.col("driver_code") == "AAA")
        .then(pl.lit("SOFT"))
        .otherwise(pl.lit("MEDIUM"))
        .alias("compound")
    )
    with pytest.raises(UnidentifiableFitError):
        fit_session(frame)


def test_a_session_with_nothing_clean_in_it_has_no_fit():
    frame = session().with_columns(pl.lit("4").alias("track_status"))
    assert fit_session(frame) is None


def test_a_driver_short_of_clean_laps_is_left_out_rather_than_guessed():
    frame = session().filter((pl.col("driver_code") != "FFF") | (pl.col("lap") > TOTAL_LAPS - 3))
    fit = fit_session(frame)
    assert fit is not None
    assert "FFF" not in {d.driver_code for d in fit.drivers}


@pytest.mark.parametrize(("tyre", "progress"), [(1, 0), (8, 20), (15, 40)])
def test_the_gap_between_two_drivers_does_not_depend_on_the_reference_state(
    monkeypatch, tyre, progress
):
    # shared slopes cancel in a difference, so the reference is a display choice and nothing
    # more. under per-driver slopes it would not cancel and this would be a modelling knob
    monkeypatch.setattr("pitadvisor.features.clean_pace.REFERENCE_TYRE_AGE", tyre)
    monkeypatch.setattr("pitadvisor.features.clean_pace.REFERENCE_LAPS_REMAINING", progress)
    fit = fit_session(session())
    assert fit is not None
    paced = {d.driver_code: d.clean_pace_millis for d in fit.drivers}
    assert paced["DDD"] - paced["AAA"] == pytest.approx(DRIVERS["DDD"], abs=60)


def test_the_benchmark_is_the_best_three_not_the_single_best():
    fit = fit_session(session())
    assert fit is not None
    fastest = sorted(d.clean_pace_millis for d in fit.drivers)
    assert fit.benchmark_millis == pytest.approx(float(np.mean(fastest[:3])))
    assert fit.benchmark_millis > fastest[0]


def test_the_cars_quicker_than_the_benchmark_read_negative():
    fit = fit_session(session())
    assert fit is not None
    ordered = sorted(fit.drivers, key=lambda d: d.clean_pace_millis)
    assert ordered[0].percent_off_benchmark < 0
    assert ordered[-1].percent_off_benchmark > 0
    for driver in fit.drivers:
        expected = 100 * (driver.clean_pace_millis - fit.benchmark_millis) / fit.benchmark_millis
        assert driver.percent_off_benchmark == pytest.approx(expected)


def test_the_benchmark_survives_one_wild_estimate():
    """The point of trimming: a single bad car must not drag the whole table with it."""
    frame = session()
    steady = fit_session(frame)
    nobbled = fit_session(
        frame.with_columns(
            pl.when(pl.col("driver_code") == "AAA")
            .then(pl.col("lap_time_millis") - 4000)
            .otherwise(pl.col("lap_time_millis"))
            .alias("lap_time_millis")
        )
    )
    assert steady is not None
    assert nobbled is not None
    moved = abs(nobbled.benchmark_millis - steady.benchmark_millis)
    assert moved < 4000 / 2


def one_stint(noise_millis: float = 120.0, seed: int = 3) -> pl.DataFrame:
    """A race nobody pitted on, which is what a heavily filtered real race looks like once
    only one stint per driver survives the exclusions."""
    rng = np.random.default_rng(seed)
    rows = []
    for position, (code, effect) in enumerate(DRIVERS.items(), start=1):
        for lap in range(1, TOTAL_LAPS + 1):
            rows.append(
                {
                    "season": 2024,
                    "round": 5,
                    "session": "race",
                    "driver_code": code,
                    "lap": lap,
                    "lap_time_millis": int(
                        BASE
                        + effect
                        + B_TYRE * lap
                        + B_PROGRESS * (TOTAL_LAPS - lap)
                        + rng.normal(0, noise_millis)
                    ),
                    "stint": 1,
                    "lap_in_stint": lap,
                    "compound": "MEDIUM",
                    "is_deleted": False,
                    "is_accurate": True,
                    "track_status": "1",
                    "pit_in": False,
                    "pit_out": False,
                    "position": position,
                }
            )
    return pl.DataFrame(rows)


def test_a_design_one_column_short_drops_the_progress_slope_instead_of_refusing():
    fit = fit_session(one_stint())
    assert fit is not None
    assert fit.b_progress_millis is None
    # the two slopes are one column, so what is left is their difference
    assert fit.b_tyre_millis == pytest.approx(B_TYRE - B_PROGRESS, abs=12)


def test_the_driver_gaps_survive_the_absorbed_slope():
    fit = fit_session(one_stint())
    assert fit is not None
    paced = {driver.driver_code: driver.clean_pace_millis for driver in fit.drivers}
    anchor = paced["AAA"]
    for code, effect in DRIVERS.items():
        assert paced[code] - anchor == pytest.approx(effect, abs=60)


def test_the_normalisation_is_unmoved_by_the_absorbed_slope():
    fit = fit_session(one_stint())
    assert fit is not None
    ordered = [driver.driver_code for driver in fit.drivers]
    assert ordered == list(DRIVERS)


def test_a_confound_the_progress_slope_cannot_explain_still_refuses():
    frame = one_stint().with_columns(
        pl.when(pl.col("driver_code") == "AAA")
        .then(pl.lit("SOFT"))
        .otherwise(pl.lit("MEDIUM"))
        .alias("compound")
    )
    with pytest.raises(UnidentifiableFitError):
        fit_session(frame)
