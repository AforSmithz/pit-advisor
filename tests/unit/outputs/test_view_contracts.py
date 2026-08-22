import json
from datetime import UTC, datetime

from pitadvisor.ingest.ratelimit import BucketState
from pitadvisor.ingest.raw_store import write_bronze, write_quarantine
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION, emit, pipeline_view
from pitadvisor.quality.checks import Status, report
from pitadvisor.quality.contracts import Quarantined, Reason, ResultRow
from pitadvisor.types import EventKey, Layer, Source

KEY = EventKey(season=2024, round=5)
NOW = datetime(2024, 5, 6, 12, tzinfo=UTC)


def result(driver="norris"):
    return ResultRow(
        run_id="run-1",
        ingested_at=NOW,
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


def state(tokens=42.0):
    return BucketState(
        name="jolpica",
        capacity=200,
        tokens=tokens,
        refill_per_second=200 / 3600,
        updated_at=NOW,
    )


def test_the_view_names_its_schema(store):
    write_bronze(store, "results", KEY, [result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [state()], NOW)
    assert view.view == "pipeline_view"
    assert view.schema_version == SCHEMA_VERSION


def test_tables_are_attributed_to_their_source(store):
    write_bronze(store, "results", KEY, [result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [], NOW)
    assert view.tables[0].source is Source.JOLPICA
    assert view.tables[0].status is Status.OK


def test_a_failing_check_makes_the_view_unhealthy(store):
    write_bronze(store, "results", KEY, [result(), result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [], NOW)
    assert view.tables[0].status is Status.FAIL
    assert view.healthy is False


def test_quota_is_reported_as_tokens_left(store):
    write_bronze(store, "results", KEY, [result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [state(37.5)], NOW)
    assert view.quota[0].tokens_left == 37.5
    assert view.quota[0].capacity == 200


def test_quarantine_reasons_reach_the_view(store):
    write_bronze(store, "results", KEY, [result()])
    write_quarantine(
        store,
        "results",
        KEY,
        "run-1",
        [Quarantined(table="results", reason=Reason.CONTRACT, detail="grid", payload={})],
    )
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [], NOW)
    assert view.quarantine[0].reason == "contract_violation"
    assert view.quarantine[0].rows == 1


def test_emit_writes_json_under_views(store):
    write_bronze(store, "results", KEY, [result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [state()], NOW)
    emit(store, view)
    body = json.loads(store.get("views/pipeline_view.json"))
    assert body["run_id"] == "run-1"
    assert body["quota"][0]["name"] == "jolpica"


def test_the_view_is_json_serialisable_end_to_end(store):
    write_bronze(store, "results", KEY, [result()])
    view = pipeline_view(report(store, Layer.BRONZE, now=NOW), "run-1", [state()], NOW)
    assert json.loads(view.model_dump_json())["generated_at"].startswith("2024-05-06")
