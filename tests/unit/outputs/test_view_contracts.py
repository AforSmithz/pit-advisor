import json
from datetime import UTC, datetime

from pitadvisor.features.assemble import assemble, event_at
from pitadvisor.ingest.ratelimit import BucketState
from pitadvisor.ingest.raw_store import write_bronze, write_quarantine
from pitadvisor.outputs.view_contracts import (
    SCHEMA_VERSION,
    driver_view,
    emit,
    pipeline_view,
    track_view,
    view_key,
    weekend_view,
)
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


def assembled_for(built, round_=6, season=2024):
    context = event_at(built.store, season, round_)
    return assemble(built.store, context, "run-1", generated_at=NOW)


def test_the_weekend_view_names_itself_and_its_run(seeded):
    view = weekend_view(assembled_for(seeded()))
    assert view.view == "weekend_view"
    assert view.schema_version == SCHEMA_VERSION
    assert view.run_id == "run-1"
    assert view.event.circuit_id == "suzuka"


def test_every_weekend_number_arrives_with_its_interval(seeded):
    view = weekend_view(assembled_for(seeded(wet_rounds=(2, 3))))
    carried = [
        estimate
        for driver in view.drivers
        for estimate in (driver.form, driver.quali_race, driver.wet)
        if estimate is not None
    ]
    assert carried
    for estimate in carried:
        assert estimate.low <= estimate.value <= estimate.high


def test_the_weekend_view_covers_every_driver_and_team(seeded):
    built = seeded()
    view = weekend_view(assembled_for(built))
    assert [driver.driver_code for driver in view.drivers] == sorted(built.codes)
    assert [team.constructor_id for team in view.teams] == sorted(built.teams)


def test_every_driver_says_when_he_was_last_in_a_car(seeded):
    built = seeded()
    view = weekend_view(assembled_for(built))
    for driver in view.drivers:
        assert driver.last_season >= 2021
        assert driver.last_race_date <= built.held(2024, 6)


def test_a_driver_who_stopped_racing_keeps_his_older_last_season(seeded):
    built = seeded()
    assembled = assembled_for(built)
    latest = assembled.pace.filter(
        assembled.pace["driver_code"] == assembled.pace["driver_code"][0]
    )
    retired = str(latest["driver_code"][0])
    seen = {driver.driver_code: driver for driver in weekend_view(assembled).drivers}
    rows = assembled.pace.filter(assembled.pace["driver_code"] == retired).sort("race_date")
    assert seen[retired].last_season == int(rows["season"][-1])
    assert seen[retired].last_race_date == rows["race_date"][-1]


def test_the_weekend_view_carries_the_track_fit_from_both_estimators(seeded):
    view = weekend_view(assembled_for(seeded()))
    for team in view.teams:
        assert team.track_fit_regression is not None
        assert team.track_fit_similarity is not None
        assert team.estimators_disagree is False


def test_the_weekend_view_says_how_stale_and_how_covered_it_is(seeded):
    built = seeded()
    view = weekend_view(assembled_for(built))
    assert view.as_of == built.held(2024, 6)
    assert view.coverage.sessions_fitted == built.events
    assert 0.0 <= view.cause_coverage <= 1.0
    assert view.weather is not None


def test_a_driver_component_travels_with_the_form_number(seeded):
    view = weekend_view(assembled_for(seeded()))
    assert all(driver.form_component is not None for driver in view.drivers)
    assert len({driver.form_component for driver in view.drivers}) == 3


def test_the_driver_view_carries_a_pace_history_that_stops_at_the_as_of(seeded):
    built = seeded()
    view = driver_view(assembled_for(built))
    cutoff = built.held(2024, 6)
    assert view.view == "driver_view"
    for driver in view.drivers:
        assert driver.pace
        assert all(sample.race_date < cutoff for sample in driver.pace)


def test_the_driver_view_carries_the_teammate_delta_over_time(seeded):
    built = seeded()
    view = driver_view(assembled_for(built))
    found = next(driver for driver in view.drivers if driver.driver_code == "AAA")
    assert {sample.teammate for sample in found.teammate} == {"AAB"}
    assert len(found.teammate) == built.events - 1
    # AAA is the quicker seat in the fixture, so every delta is negative
    assert all(sample.delta < 0 for sample in found.teammate)


def test_the_track_view_carries_the_circuit_profile_and_its_lookalikes(seeded):
    view = track_view(assembled_for(seeded()))
    assert view.view == "track_view"
    assert view.profile.circuit_id == "suzuka"
    assert view.neighbours[0].circuit_id == "suzuka"
    assert len(view.neighbours) > 1


def test_the_track_view_lists_what_each_team_has_done_here(seeded):
    built = seeded()
    view = track_view(assembled_for(built))
    assert [team.constructor_id for team in view.teams] == sorted(built.teams)
    for team in view.teams:
        assert all(sample.circuit_id == "suzuka" for sample in team.history)
        assert len(team.history) == len(built.seasons) * 2 - 2


def test_every_event_view_round_trips_through_the_store(store, seeded):
    assembled = assembled_for(seeded())
    for build in (weekend_view, driver_view, track_view):
        view = build(assembled)
        emit(store, view)
        payload = json.loads(store.get(view_key(view.view)))
        assert payload["view"] == view.view
        assert payload["run_id"] == "run-1"
        assert payload["schema_version"] == SCHEMA_VERSION
