import json
from datetime import UTC, datetime

import polars as pl
import pytest

from pitadvisor.ingest.raw_store import (
    LocalObjectStore,
    RawOverwriteError,
    RawStore,
    S3ObjectStore,
    write_bronze,
    write_quarantine,
)
from pitadvisor.quality.contracts import Quarantined, RaceRow, Reason
from pitadvisor.types import EventKey, Provenance, SessionKey, SessionKind, Source

KEY = EventKey(season=2024, round=5)


def provenance(url="https://api.jolpi.ca/x", fetched_at=None):
    return Provenance(
        run_id="run-1",
        source=Source.JOLPICA,
        url=url,
        fetched_at=fetched_at or datetime(2024, 5, 5, 12, tzinfo=UTC),
        status=200,
    )


def test_local_put_get_exists(store):
    store.put("raw/x.json", b"{}")
    assert store.exists("raw/x.json")
    assert store.get("raw/x.json") == b"{}"
    assert not store.exists("raw/missing.json")


def test_local_list_filters_by_prefix(store):
    store.put("raw/source=jolpica/a.json", b"1")
    store.put("bronze/table=races/b.parquet", b"2")
    keys = [item.key for item in store.list("raw/")]
    assert keys == ["raw/source=jolpica/a.json"]


def test_land_writes_payload_and_sidecar(raw, store):
    uri = raw.land(KEY, "results", b'{"ok":1}', provenance())
    assert uri.endswith("results-20240505T120000000Z.json")
    key = uri.removeprefix("file://").split("lake/", 1)[1]
    assert store.get(key) == b'{"ok":1}'
    meta = json.loads(store.get(key + ".meta.json"))
    assert meta["run_id"] == "run-1"
    assert meta["status"] == 200


def test_land_refuses_to_overwrite(raw):
    raw.land(KEY, "results", b"1", provenance())
    with pytest.raises(RawOverwriteError):
        raw.land(KEY, "results", b"2", provenance())


def test_relanding_identical_bytes_is_idempotent(raw, store):
    first = raw.land(KEY, "results", b"1", provenance())
    again = raw.land(KEY, "results", b"1", provenance())
    assert first.endswith(again)


def test_a_second_fetch_lands_beside_the_first(raw):
    raw.land(KEY, "results", b"1", provenance())
    raw.land(KEY, "results", b"2", provenance(fetched_at=datetime(2024, 5, 8, 9, tzinfo=UTC)))
    assert len(raw.versions(Source.JOLPICA, KEY, "results")) == 2


def test_latest_returns_the_newest_payload(raw):
    raw.land(KEY, "results", b"old", provenance())
    raw.land(KEY, "results", b"new", provenance(fetched_at=datetime(2024, 5, 8, 9, tzinfo=UTC)))
    body, meta = raw.latest(Source.JOLPICA, KEY, "results")
    assert body == b"new"
    assert meta.source is Source.JOLPICA


def test_latest_is_none_when_nothing_landed(raw):
    assert raw.latest(Source.JOLPICA, KEY, "results") is None


def test_versions_ignores_a_sibling_resource(raw):
    raw.land(KEY, "results", b"1", provenance())
    raw.land(KEY, "resultsextra", b"2", provenance())
    assert len(raw.versions(Source.JOLPICA, KEY, "results-")) == 1


def test_write_bronze_round_trips(store):
    rows = [
        RaceRow(
            run_id="run-1",
            ingested_at=datetime(2024, 5, 5, tzinfo=UTC),
            season=2024,
            round=5,
            race_name="Synthetic",
            circuit_id="synthetica",
            circuit_name="Synthetica Ring",
            latitude=1.0,
            longitude=2.0,
            race_date=datetime(2024, 5, 5).date(),
        )
    ]
    uri = write_bronze(store, "races", KEY, rows)
    assert "bronze/table=races/season=2024/round=05/races.parquet" in uri
    frame = pl.read_parquet(store.get("bronze/table=races/season=2024/round=05/races.parquet"))
    assert frame.height == 1
    assert frame["circuit_id"][0] == "synthetica"


def test_bronze_key_carries_the_session_for_session_scoped_tables(store):
    key = SessionKey(season=2024, round=5, session=SessionKind.RACE)
    uri = write_bronze(
        store,
        "session_laps",
        key,
        [
            RaceRow(
                run_id="r",
                ingested_at=datetime(2024, 5, 5, tzinfo=UTC),
                season=2024,
                round=5,
                race_name="x",
                circuit_id="c",
                circuit_name="c",
                latitude=0.0,
                longitude=0.0,
                race_date=datetime(2024, 5, 5).date(),
            )
        ],
    )
    assert "session=race" in uri


def test_write_quarantine_is_a_noop_without_rows(store):
    assert write_quarantine(store, "laps", KEY, "run-1", []) is None


def test_write_quarantine_emits_one_json_line_per_row(store):
    rows = [
        Quarantined(table="laps", reason=Reason.CONTRACT, detail="lap: missing", payload={"a": 1}),
        Quarantined(table="laps", reason=Reason.CONTRACT, detail="lap: bad", payload={"a": 2}),
    ]
    write_quarantine(store, "laps", KEY, "run-1", rows)
    body = store.get("quarantine/table=laps/season=2024/round=05/run=run-1.jsonl").decode()
    assert [json.loads(line)["payload"]["a"] for line in body.splitlines()] == [1, 2]


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def get_paginator(self, name):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket, Prefix):
                yield {
                    "Contents": [
                        {"Key": key, "Size": len(body), "LastModified": datetime.now(UTC)}
                        for key, body in objects.items()
                        if key.startswith(Prefix)
                    ]
                }

        return Paginator()


def test_s3_store_speaks_the_same_protocol():
    s3 = S3ObjectStore("bucket", FakeS3())
    assert s3.put("raw/a.json", b"1") == "s3://bucket/raw/a.json"
    assert s3.exists("raw/a.json")
    assert not s3.exists("raw/b.json")
    assert s3.get("raw/a.json") == b"1"
    assert [item.key for item in s3.list("raw/")] == ["raw/a.json"]


def test_raw_store_works_over_s3_too():
    raw = RawStore(S3ObjectStore("bucket", FakeS3()))
    raw.land(KEY, "results", b"1", provenance())
    body, _ = raw.latest(Source.JOLPICA, KEY, "results")
    assert body == b"1"


def test_local_store_clear(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put("bronze/table=races/x.parquet", b"1")
    store.clear("bronze/")
    assert not store.exists("bronze/table=races/x.parquet")
