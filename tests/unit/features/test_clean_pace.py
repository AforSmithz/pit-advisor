import polars as pl
import pytest

from pitadvisor.features.clean_pace import (
    Reason,
    classify,
    clean,
    exclusion_counts,
    exclusion_rate,
    is_green,
    with_gap_ahead,
    with_laps_remaining,
)

BASE = {
    "season": 2024,
    "round": 5,
    "session": "race",
    "driver_code": "VER",
    "lap": 10,
    "lap_time_millis": 93_400,
    "stint": 1,
    "lap_in_stint": 5,
    "compound": "MEDIUM",
    "is_deleted": False,
    "is_accurate": True,
    "track_status": "1",
    "pit_in": False,
    "pit_out": False,
    "position": 1,
}


# the same dtypes silver hands over, so an all-null column in a one row case stays an int
SCHEMA = {
    "season": pl.Int64,
    "round": pl.Int64,
    "session": pl.String,
    "driver_code": pl.String,
    "lap": pl.Int64,
    "lap_time_millis": pl.Int64,
    "stint": pl.Int64,
    "lap_in_stint": pl.Int64,
    "compound": pl.String,
    "is_deleted": pl.Boolean,
    "is_accurate": pl.Boolean,
    "track_status": pl.String,
    "pit_in": pl.Boolean,
    "pit_out": pl.Boolean,
    "position": pl.Int64,
}


def laps(*overrides: dict[str, object]) -> pl.DataFrame:
    return pl.DataFrame([{**BASE, **row} for row in (overrides or ({},))], schema=SCHEMA)


def reasons(frame: pl.DataFrame) -> list[str | None]:
    return classify(frame).sort("driver_code", "lap")["exclusion"].to_list()


@pytest.mark.parametrize(
    ("status", "green"),
    [("1", True), ("11", True), ("14", False), ("4", False), ("2", False), ("67", False)],
)
def test_a_lap_is_green_only_when_every_flag_is_green(status, green):
    frame = pl.DataFrame({"track_status": [status]})
    assert frame.select(is_green(pl.col("track_status")))["track_status"][0] is green


def test_a_null_track_status_is_not_green():
    frame = pl.DataFrame({"track_status": [None]}, schema={"track_status": pl.String})
    assert frame.select(is_green(pl.col("track_status")))["track_status"][0] is False


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"lap_time_millis": None}, Reason.NO_LAP_TIME),
        ({"is_deleted": True}, Reason.DELETED),
        ({"lap_in_stint": 1}, Reason.OUT_LAP),
        ({"pit_out": True}, Reason.OUT_LAP),
        ({"pit_in": True}, Reason.IN_LAP),
        ({"lap": 1}, Reason.OPENING_LAPS),
        ({"lap": 2}, Reason.OPENING_LAPS),
        ({"track_status": "14"}, Reason.TRACK_NOT_GREEN),
        ({"is_accurate": False}, Reason.INACCURATE),
    ],
)
def test_each_rule_names_its_own_reason(row, reason):
    assert reasons(laps(row)) == [reason]


def test_a_clean_lap_survives():
    assert reasons(laps()) == [None]


def test_the_leader_is_never_in_traffic():
    assert reasons(laps({"position": 1})) == [None]


def test_a_car_close_behind_is_dropped_as_traffic():
    frame = laps(
        {"driver_code": "VER", "position": 1, "lap": 10, "lap_time_millis": 90_000},
        {"driver_code": "NOR", "position": 2, "lap": 10, "lap_time_millis": 90_800},
    )
    assert reasons(frame) == [Reason.TRAFFIC, None]  # sorted by code, nor before ver


def test_a_car_far_behind_keeps_its_lap():
    frame = laps(
        {"driver_code": "VER", "position": 1, "lap": 10, "lap_time_millis": 90_000},
        {"driver_code": "NOR", "position": 2, "lap": 10, "lap_time_millis": 96_000},
    )
    assert reasons(frame) == [None, None]


def test_the_threshold_is_a_parameter_not_a_law():
    frame = laps(
        {"driver_code": "VER", "position": 1, "lap": 10, "lap_time_millis": 90_000},
        {"driver_code": "NOR", "position": 2, "lap": 10, "lap_time_millis": 96_000},
    )
    assert classify(frame, traffic_threshold_millis=10_000).sort("driver_code")[
        "exclusion"
    ].to_list() == [Reason.TRAFFIC, None]


def test_a_missing_lap_time_poisons_every_elapsed_after_it():
    frame = laps(
        {"driver_code": "VER", "position": 1, "lap": 3, "lap_time_millis": 90_000},
        {"driver_code": "VER", "position": 1, "lap": 4, "lap_time_millis": 90_000},
        {"driver_code": "NOR", "position": 2, "lap": 3, "lap_time_millis": None},
        {"driver_code": "NOR", "position": 2, "lap": 4, "lap_time_millis": 95_000},
    )
    assert reasons(frame) == [Reason.NO_LAP_TIME, Reason.GAP_UNKNOWN, None, None]


def test_every_excluded_lap_gets_exactly_one_reason():
    frame = laps(
        {"lap": 1, "driver_code": "VER"},
        {"lap": 3, "driver_code": "VER", "is_deleted": True, "pit_in": True},
        {"lap": 4, "driver_code": "VER"},
        {"lap": 5, "driver_code": "VER", "track_status": "4", "is_accurate": False},
    )
    classified = classify(frame)
    counts = exclusion_counts(classified)
    assert sum(counts.values()) == classified["exclusion"].is_not_null().sum()
    assert counts == {Reason.DELETED: 1, Reason.OPENING_LAPS: 1, Reason.TRACK_NOT_GREEN: 1}


def test_clean_keeps_only_the_unexcluded():
    frame = laps({"lap": 1, "driver_code": "VER"}, {"lap": 9, "driver_code": "VER"})
    assert clean(classify(frame))["lap"].to_list() == [9]


def test_an_empty_session_classifies_to_nothing():
    empty = pl.DataFrame(schema=SCHEMA)
    assert classify(empty).height == 0
    assert exclusion_counts(classify(empty)) == {}
    assert exclusion_rate(classify(empty)) == 0.0


def test_the_exclusion_rate_is_reported_for_the_whole_frame():
    frame = laps({"lap": 1, "driver_code": "VER"}, {"lap": 9, "driver_code": "VER"})
    assert exclusion_rate(classify(frame)) == 0.5
    assert exclusion_rate(classify(laps())) == 0.0


def test_laps_remaining_counts_down_to_the_flag():
    frame = laps(
        {"lap": 8, "driver_code": "VER"},
        {"lap": 9, "driver_code": "VER"},
        {"lap": 10, "driver_code": "VER"},
    )
    assert with_laps_remaining(frame).sort("lap")["laps_remaining"].to_list() == [2, 1, 0]


def test_the_gap_is_measured_against_the_car_one_place_ahead():
    frame = laps(
        {"driver_code": "VER", "position": 1, "lap": 3, "lap_time_millis": 90_000},
        {"driver_code": "NOR", "position": 2, "lap": 3, "lap_time_millis": 92_000},
        {"driver_code": "HAM", "position": 3, "lap": 3, "lap_time_millis": 95_000},
    )
    gaps = with_gap_ahead(frame).sort("position")["gap_ahead_millis"].to_list()
    assert gaps == [None, 2_000, 3_000]
