from datetime import UTC, datetime, timedelta

from pitadvisor.ingest.raw_store import write_bronze, write_quarantine
from pitadvisor.quality.checks import Status, report
from pitadvisor.quality.contracts import LapRow, PitStopRow, Quarantined, Reason, ResultRow
from pitadvisor.types import EventKey, Layer

KEY = EventKey(season=2024, round=5)
NOW = datetime(2024, 5, 6, 12, tzinfo=UTC)


def result(driver="norris", ingested_at=NOW):
    return ResultRow(
        run_id="run-1",
        ingested_at=ingested_at,
        season=2024,
        round=5,
        driver_id=driver,
        constructor_id="mclaren",
        grid=3,
        position=2,
        position_text="2",
        points=18,
        laps_completed=57,
        status="Finished",
    )


def lap(driver="norris", number=1, ingested_at=NOW):
    return LapRow(
        run_id="run-1",
        ingested_at=ingested_at,
        season=2024,
        round=5,
        driver_id=driver,
        lap=number,
        position=2,
        time_millis=93401,
    )


def outcome_for(result_report, check, table):
    return next(
        item for item in result_report.outcomes if item.check == check and item.table == table
    )


def test_an_empty_layer_fails(store):
    found = report(store, Layer.BRONZE, now=NOW)
    assert not found.ok
    assert found.outcomes[0].detail.endswith("is empty")


def test_a_clean_layer_passes(store):
    write_bronze(store, "results", KEY, [result()])
    write_bronze(store, "laps", KEY, [lap()])
    found = report(store, Layer.BRONZE, now=NOW)
    assert found.ok
    assert outcome_for(found, "row_count", "laps").detail == "1 rows"


def test_freshness_warns_on_stale_data(store):
    write_bronze(store, "results", KEY, [result(ingested_at=NOW - timedelta(days=30))])
    found = report(store, Layer.BRONZE, now=NOW)
    assert outcome_for(found, "freshness", "results").status is Status.WARN


def test_freshness_is_reported_in_hours(store):
    write_bronze(store, "results", KEY, [result(ingested_at=NOW - timedelta(hours=5))])
    found = report(store, Layer.BRONZE, now=NOW)
    assert outcome_for(found, "freshness", "results").detail.startswith("5.0h")


def test_a_duplicate_natural_key_fails(store):
    write_bronze(store, "results", KEY, [result(), result()])
    found = report(store, Layer.BRONZE, now=NOW)
    assert outcome_for(found, "duplicate_key", "results").status is Status.FAIL
    assert not found.ok


def test_a_lap_for_an_unknown_driver_fails_the_reference(store):
    write_bronze(store, "results", KEY, [result("norris")])
    write_bronze(store, "laps", KEY, [lap("a_driver_who_did_not_start")])
    found = report(store, Layer.BRONZE, now=NOW)
    referential = outcome_for(found, "referential", "laps")
    assert referential.status is Status.FAIL
    assert "1 rows" in referential.detail


def test_references_are_skipped_when_the_parent_is_absent(store):
    write_bronze(store, "laps", KEY, [lap()])
    found = report(store, Layer.BRONZE, now=NOW)
    assert not any(item.check == "referential" for item in found.outcomes)


def test_quarantine_rows_are_counted_by_reason(store):
    write_bronze(store, "results", KEY, [result()])
    write_quarantine(
        store,
        "results",
        KEY,
        "run-1",
        [
            Quarantined(table="results", reason=Reason.CONTRACT, detail="grid", payload={}),
            Quarantined(table="results", reason=Reason.CONTRACT, detail="grid", payload={}),
        ],
    )
    found = report(store, Layer.BRONZE, now=NOW)
    assert found.quarantine[0].rows == 2
    assert found.quarantine[0].explained


def test_an_unknown_reason_is_flagged_as_unexplained(store):
    write_bronze(store, "results", KEY, [result()])
    store.put(
        "quarantine/table=results/season=2024/round=05/run=x.jsonl",
        b'{"table": "results", "reason": "something_new", "detail": "", "payload": {}}',
    )
    found = report(store, Layer.BRONZE, now=NOW)
    assert not found.quarantine[0].explained
    assert not found.ok


def test_the_report_spans_several_events(store):
    write_bronze(store, "results", KEY, [result()])
    write_bronze(store, "results", EventKey(season=2024, round=6), [result("verstappen")])
    found = report(store, Layer.BRONZE, now=NOW)
    assert outcome_for(found, "row_count", "results").detail == "2 rows"


def pitstop(driver="norris", duration=20315):
    return PitStopRow(
        run_id="run-1",
        ingested_at=NOW,
        season=2024,
        round=5,
        driver_id=driver,
        stop=1,
        lap=18,
        time_of_day="14:03:30",
        duration_millis=duration,
    )


def test_a_stop_without_a_duration_is_kept(store):
    write_bronze(store, "results", KEY, [result()])
    write_bronze(store, "pitstops", KEY, [pitstop(duration=None)])
    found = report(store, Layer.BRONZE, now=NOW)
    assert outcome_for(found, "row_count", "pitstops").detail == "1 rows"
    assert found.quarantine == []


def test_a_high_null_rate_warns(store):
    write_bronze(store, "results", KEY, [result()])
    write_bronze(store, "pitstops", KEY, [pitstop(duration=None)])
    null_rate = outcome_for(report(store, Layer.BRONZE, now=NOW), "null_rate", "pitstops")
    assert null_rate.status is Status.WARN
    assert null_rate.detail == "1/1 rows have no duration_millis"


def test_a_full_column_does_not_warn(store):
    write_bronze(store, "results", KEY, [result()])
    write_bronze(store, "pitstops", KEY, [pitstop()])
    assert outcome_for(report(store, Layer.BRONZE, now=NOW), "null_rate", "pitstops").status is (
        Status.OK
    )
