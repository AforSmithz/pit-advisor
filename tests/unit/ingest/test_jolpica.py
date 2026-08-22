import json
from datetime import UTC, datetime

import polars as pl
import pytest

from pitadvisor.ingest.jolpica import (
    JolpicaClient,
    RawMissingError,
    backfill,
    endpoint,
    ingest_event,
    parse_laps,
    parse_pitstops,
    parse_qualifying,
    parse_races,
    parse_results,
    plan,
)
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Source

KEY = EventKey(season=2024, round=5)
STAMP = {"run_id": "run-1", "ingested_at": datetime(2024, 5, 6, tzinfo=UTC)}


def client(raw, ledger, fetch, run_id="run-1"):
    return JolpicaClient(raw, ledger, None, run_id, fetch)


def test_endpoint_for_the_schedule():
    assert endpoint(2024, None, "races") == (
        "https://api.jolpi.ca/ergast/f1/2024.json?limit=100&offset=0"
    )


def test_endpoint_for_an_event_resource():
    assert endpoint(2024, 5, "laps", 200) == (
        "https://api.jolpi.ca/ergast/f1/2024/5/laps.json?limit=100&offset=200"
    )


def test_plan_covers_every_round_for_event_resources():
    urls = plan(2024, [1, 2], ["races", "results"])
    assert len(urls) == 3
    assert sum("results" in url for url in urls) == 2


def test_parse_races(payload):
    rows = parse_races([payload("jolpica/races.json")], STAMP)
    assert rows[0]["circuit_id"] == "synthetica"
    assert rows[0]["latitude"] == "1.2914"
    assert rows[0]["start_utc"] == datetime(2024, 5, 5, 13, tzinfo=UTC)


def test_parse_races_without_a_time_leaves_the_start_null(payload):
    payload = payload("jolpica/races.json")
    del payload["MRData"]["RaceTable"]["Races"][0]["time"]
    assert parse_races([payload], STAMP)[0]["start_utc"] is None


def test_parse_results_reads_the_fastest_lap(payload):
    rows = parse_results([payload("jolpica/results.json")], STAMP)
    assert len(rows) == 3
    assert rows[0]["driver_id"] == "verstappen"
    assert rows[0]["fastest_lap_millis"] == 92608


def test_parse_qualifying_converts_every_segment(payload):
    rows = parse_qualifying([payload("jolpica/qualifying.json")], STAMP)
    assert rows[0]["q1_millis"] == 91500
    assert rows[0]["q3_millis"] == 90100


def test_parse_laps_flattens_the_timings(payload):
    rows = parse_laps([payload("jolpica/laps.json")], STAMP)
    assert len(rows) == 9
    assert rows[0]["lap"] == "1"
    assert rows[0]["time_millis"] == 93401


def test_parse_pitstops(payload):
    rows = parse_pitstops([payload("jolpica/pitstops.json")], STAMP)
    assert rows[0]["duration_millis"] == 20315


def test_ingest_lands_raw_and_writes_bronze(store, raw, ledger, fetch):
    outcomes = ingest_event(client(raw, ledger, fetch), store, KEY, ["results"])
    assert outcomes[0].rows == 3
    assert outcomes[0].quarantined == 0
    assert store.exists("bronze/table=results/season=2024/round=05/results.parquet")
    assert raw.latest(Source.JOLPICA, KEY, "results-offset0000") is not None


def test_bronze_rows_carry_the_run_id(store, raw, ledger, fetch):
    ingest_event(client(raw, ledger, fetch, "run-42"), store, KEY, ["results"])
    frame = pl.read_parquet(store.get("bronze/table=results/season=2024/round=05/results.parquet"))
    assert frame["run_id"].unique().to_list() == ["run-42"]


def test_a_bad_row_is_quarantined_not_dropped(store, raw, ledger, payload, fetch_factory):
    payload = payload("jolpica/results.json")
    payload["MRData"]["RaceTable"]["Races"][0]["Results"][1]["grid"] = "nonsense"
    fetch = fetch_factory(bodies={"results": payload})
    outcomes = ingest_event(client(raw, ledger, fetch), store, KEY, ["results"])
    assert outcomes[0].rows == 2
    assert outcomes[0].quarantined == 1
    body = store.get("quarantine/table=results/season=2024/round=05/run=run-1.jsonl").decode()
    assert json.loads(body)["reason"] == "contract_violation"


def test_pagination_follows_the_total(store, raw, ledger, payload, fetch_factory):
    first = payload("jolpica/laps.json")
    first["MRData"]["total"] = "150"
    fetch = fetch_factory(bodies={"laps": first})
    outcomes = ingest_event(client(raw, ledger, fetch), store, KEY, ["laps"])
    assert outcomes[0].requests == 2
    assert len(fetch.calls) == 2


def test_a_304_reads_the_cached_payload_back(store, raw, ledger, fetch_factory):
    ingest_event(client(raw, ledger, fetch_factory()), store, KEY, ["results"])
    store.clear("bronze/")
    outcomes = ingest_event(client(raw, ledger, fetch_factory(status=304)), store, KEY, ["results"])
    assert outcomes[0].not_modified
    assert outcomes[0].rows == 3
    assert outcomes[0].raw_objects == []


def test_a_304_with_nothing_in_raw_refetches(store, raw, ledger, fetch_factory):
    class OnceStale(fetch_factory):
        def __call__(self, url, ledger, limiter=None, **kwargs):
            self.status = 200 if self.calls else 304
            return super().__call__(url, ledger, limiter, **kwargs)

    stale = OnceStale(status=304)
    outcomes = ingest_event(client(raw, ledger, stale), store, KEY, ["results"])
    assert outcomes[0].rows == 3
    assert len(stale.calls) == 2


def test_a_second_304_on_the_refetch_is_an_error(store, raw, ledger, fetch_factory):
    with pytest.raises(RawMissingError):
        ingest_event(client(raw, ledger, fetch_factory(status=304)), store, KEY, ["results"])


def test_skip_present_costs_no_request(store, raw, ledger, fetch):
    ingest_event(client(raw, ledger, fetch), store, KEY, ["results"])
    calls = len(fetch.calls)
    outcomes = ingest_event(client(raw, ledger, fetch), store, KEY, ["results"], skip_present=True)
    assert outcomes[0].skipped == "already in bronze"
    assert len(fetch.calls) == calls


def test_backfill_walks_the_schedule(store, raw, ledger, fetch):
    outcomes = backfill(client(raw, ledger, fetch), store, 2024, ["races", "results"])
    tables = {outcome.table for outcome in outcomes}
    assert tables == {"races", "results"}
    assert any(outcome.round == 5 for outcome in outcomes)


def test_backfill_reruns_are_free(store, raw, ledger, fetch):
    backfill(client(raw, ledger, fetch), store, 2024, ["races", "results"])
    calls = len(fetch.calls)
    outcomes = backfill(client(raw, ledger, fetch), store, 2024, ["races", "results"])
    # the schedule is still fetched to learn the rounds, everything else is skipped
    assert len(fetch.calls) == calls + 1
    assert all(outcome.skipped for outcome in outcomes if outcome.table == "results")


def test_the_raw_store_is_shared_across_resources(store, raw, ledger, fetch):
    ingest_event(client(raw, ledger, fetch), store, KEY, ["results", "qualifying"])
    assert raw.latest(Source.JOLPICA, KEY, "qualifying-offset0000") is not None


def test_ingest_uses_a_fresh_raw_store_object_per_fetch(store, ledger, fetch, fetch_factory):
    raw = RawStore(store)
    ingest_event(client(raw, ledger, fetch), store, KEY, ["results"])
    ingest_event(client(raw, ledger, fetch_factory()), store, KEY, ["results"])
    assert len(raw.versions(Source.JOLPICA, KEY, "results-offset0000")) == 2
