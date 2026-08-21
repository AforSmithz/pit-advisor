from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pitadvisor.quality.contracts import (
    TABLES,
    LapRow,
    PitStopRow,
    Reason,
    ResultRow,
    parse_duration_millis,
    validate,
)

NOW = datetime(2024, 5, 6, tzinfo=UTC)


def base(**overrides):
    row = {"run_id": "run-1", "ingested_at": NOW, "season": 2024, "round": 5}
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1:32.608", 92608),
        ("32.608", 32608),
        ("1:30:01.000", 5401000),
        ("+5.123", 5123),
        ("24.3", 24300),
        ("90", 90000),
    ],
)
def test_durations_parse(text, expected):
    assert parse_duration_millis(text) == expected


@pytest.mark.parametrize("text", [None, "", "  ", "no time", "1:2:3:4.5"])
def test_unparseable_durations_are_none(text):
    assert parse_duration_millis(text) is None


def test_every_table_has_a_contract():
    assert set(TABLES) == {
        "races",
        "results",
        "qualifying",
        "laps",
        "pitstops",
        "weather",
        "session_laps",
    }


def test_a_good_row_validates():
    kept, dropped = validate(
        "laps", LapRow, [base(driver_id="norris", lap=1, position=2, time_millis=93401)]
    )
    assert len(kept) == 1
    assert dropped == []


def test_a_missing_field_is_quarantined_with_the_field_name():
    kept, dropped = validate("laps", LapRow, [base(driver_id="norris", lap=1, position=2)])
    assert kept == []
    assert dropped[0].reason is Reason.CONTRACT
    assert "time_millis" in dropped[0].detail


def test_the_payload_is_kept_for_replay():
    _, dropped = validate("laps", LapRow, [base(driver_id="norris")])
    assert dropped[0].payload["driver_id"] == "norris"


def test_an_unexpected_field_is_rejected():
    _, dropped = validate(
        "laps",
        LapRow,
        [base(driver_id="norris", lap=1, position=2, time_millis=1, sponsor="acme")],
    )
    assert "sponsor" in dropped[0].detail


def test_a_zero_lap_time_is_rejected():
    _, dropped = validate(
        "laps", LapRow, [base(driver_id="norris", lap=1, position=2, time_millis=0)]
    )
    assert dropped


def test_strings_that_look_like_numbers_are_coerced():
    kept, _ = validate(
        "laps",
        LapRow,
        [
            base(
                season="2024", round="5", driver_id="norris", lap="1", position="2", time_millis="9"
            )
        ],
    )
    assert kept[0].lap == 1


def test_a_result_row_keeps_the_optional_fastest_lap():
    kept, _ = validate(
        "results",
        ResultRow,
        [
            base(
                driver_id="norris",
                constructor_id="mclaren",
                grid=3,
                position=2,
                position_text="2",
                points=18,
                laps_completed=57,
                status="Finished",
            )
        ],
    )
    assert kept[0].fastest_lap_millis is None


def test_a_retirement_keeps_its_position_text():
    kept, _ = validate(
        "results",
        ResultRow,
        [
            base(
                driver_id="norris",
                constructor_id="mclaren",
                grid=3,
                position=None,
                position_text="R",
                points=0,
                laps_completed=12,
                status="Gearbox",
            )
        ],
    )
    assert kept[0].position is None
    assert kept[0].position_text == "R"


def test_rows_are_frozen():
    kept, _ = validate("laps", LapRow, [base(driver_id="norris", lap=1, position=2, time_millis=1)])
    with pytest.raises(ValidationError):
        kept[0].lap = 4


def test_a_pit_stop_without_a_duration_still_validates():
    kept, dropped = validate(
        "pitstops",
        PitStopRow,
        [base(driver_id="tsunoda", stop=1, lap=27, time_of_day="15:45:52", duration_millis=None)],
    )
    assert dropped == []
    assert kept[0].duration_millis is None


def test_a_zero_duration_is_still_rejected():
    _, dropped = validate(
        "pitstops",
        PitStopRow,
        [base(driver_id="tsunoda", stop=1, lap=27, time_of_day="15:45:52", duration_millis=0)],
    )
    assert dropped
