from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pitadvisor.types import (
    EventKey,
    IngestOutcome,
    Layer,
    Provenance,
    SessionKey,
    SessionKind,
    Source,
    bronze_key,
    quarantine_key,
    raw_filename,
    raw_key,
)


def test_layer_values():
    assert [layer.value for layer in Layer] == [
        "raw",
        "bronze",
        "silver",
        "gold",
        "views",
        "quarantine",
        "docs",
        "cache",
    ]


def test_source_values():
    assert {s.value for s in Source} == {
        "jolpica",
        "fastf1",
        "open_meteo",
        "fia_docs",
        "wikipedia",
        "curated",
    }


def test_sprint_session_kinds():
    assert {s.value for s in SessionKind} >= {
        "sprint_qualifying",
        "sprint_shootout",
        "sprint",
    }
    assert SessionKind.QUALIFYING != SessionKind.SPRINT_QUALIFYING


def test_enums_are_strings():
    assert Source.JOLPICA == "jolpica"
    assert f"{Layer.GOLD}" == "gold"


def test_event_key_frozen():
    key = EventKey(season=2024, round=5)
    with pytest.raises(ValidationError):
        key.round = 6


def test_event_key_hashable():
    a = EventKey(season=2024, round=5)
    b = EventKey(season=2024, round=5)
    assert a == b
    assert len({a, b}) == 1
    assert {a: "monaco"}[b] == "monaco"


def test_session_key_never_equals_its_event():
    event = EventKey(season=2024, round=5)
    session = SessionKey(season=2024, round=5, session=SessionKind.RACE)
    assert session.season == 2024
    assert session != event
    assert len({session, SessionKey(season=2024, round=5, session=SessionKind.RACE)}) == 1
    assert len({session, SessionKey(season=2024, round=5, session=SessionKind.QUALIFYING)}) == 2


def test_session_key_frozen():
    session = SessionKey(season=2024, round=5, session=SessionKind.RACE)
    with pytest.raises(ValidationError):
        session.session = SessionKind.SPRINT


@pytest.mark.parametrize("season", [1949, 2101, 0, -2024])
def test_season_out_of_range(season):
    with pytest.raises(ValidationError):
        EventKey(season=season, round=1)


@pytest.mark.parametrize("rnd", [0, -1, 31, 100])
def test_round_out_of_range(rnd):
    with pytest.raises(ValidationError):
        EventKey(season=2024, round=rnd)


def test_range_boundaries_are_inclusive():
    assert EventKey(season=1950, round=1).round == 1
    assert EventKey(season=2100, round=30).season == 2100


def test_unknown_session_kind():
    with pytest.raises(ValidationError):
        SessionKey(season=2024, round=5, session="warmup")


def _provenance(**overrides):
    fields = {
        "run_id": "run-2024-05-05T10:00:00",
        "source": Source.JOLPICA,
        "url": "https://api.jolpi.ca/ergast/f1/2024/5/results.json",
        "fetched_at": datetime(2024, 5, 5, 10, 0, tzinfo=UTC),
        "status": 200,
    }
    fields.update(overrides)
    return Provenance(**fields)


def test_provenance_accepts_utc():
    p = _provenance()
    assert p.fetched_at.utcoffset() == timedelta(0)
    assert p.etag is None
    assert p.source is Source.JOLPICA


def test_provenance_parses_z_suffix():
    p = _provenance(fetched_at="2024-05-05T10:00:00Z")
    assert p.fetched_at == datetime(2024, 5, 5, 10, 0, tzinfo=UTC)


def test_provenance_rejects_naive():
    with pytest.raises(ValidationError):
        _provenance(fetched_at=datetime(2024, 5, 5, 10, 0))


def test_provenance_rejects_naive_string():
    with pytest.raises(ValidationError):
        _provenance(fetched_at="2024-05-05T10:00:00")


def test_provenance_rejects_a_local_offset():
    singapore = timezone(timedelta(hours=8))
    with pytest.raises(ValidationError):
        _provenance(fetched_at=datetime(2024, 5, 5, 18, 0, tzinfo=singapore))


def test_provenance_frozen():
    p = _provenance()
    with pytest.raises(ValidationError):
        p.status = 304


def test_raw_key_for_a_session():
    key = SessionKey(season=2024, round=5, session=SessionKind.SPRINT)
    assert (
        raw_key(Source.FASTF1, key, "laps.parquet")
        == "raw/source=fastf1/season=2024/round=05/session=sprint/laps.parquet"
    )


def test_raw_key_for_an_event():
    key = EventKey(season=2024, round=5)
    assert (
        raw_key(Source.JOLPICA, key, "results.json")
        == "raw/source=jolpica/season=2024/round=05/results.json"
    )


def test_raw_key_zero_pads():
    early = raw_key(Source.JOLPICA, EventKey(season=2024, round=2), "results.json")
    late = raw_key(Source.JOLPICA, EventKey(season=2024, round=12), "results.json")
    assert "round=02" in early
    assert sorted([late, early]) == [early, late]


def test_raw_key_uses_source_value():
    key = EventKey(season=2024, round=5)
    assert raw_key(Source.OPEN_METEO, key, "forecast.json").startswith(
        "raw/source=open_meteo/season=2024/"
    )


def test_raw_filename_carries_the_fetch_time_to_the_millisecond():
    stamp = datetime(2024, 5, 5, 12, 30, 15, 123456, tzinfo=UTC)
    assert raw_filename("results", stamp) == "results-20240505T123015123Z.json"


def test_raw_filename_takes_another_suffix():
    stamp = datetime(2024, 5, 5, tzinfo=UTC)
    assert raw_filename("laps", stamp, "parquet").endswith(".parquet")


def test_bronze_key_for_an_event():
    key = EventKey(season=2024, round=5)
    assert bronze_key("results", key) == "bronze/table=results/season=2024/round=05/results.parquet"


def test_bronze_key_for_a_session():
    key = SessionKey(season=2024, round=5, session=SessionKind.SPRINT)
    assert "session=sprint/session_laps.parquet" in bronze_key("session_laps", key)


def test_quarantine_key_names_the_run():
    key = EventKey(season=2024, round=5)
    assert (
        quarantine_key("laps", key, "run-1")
        == "quarantine/table=laps/season=2024/round=05/run=run-1.jsonl"
    )


def test_ingest_outcome_defaults_to_nothing_done():
    outcome = IngestOutcome(source=Source.JOLPICA, table="laps", season=2024, round=5)
    assert outcome.rows == 0
    assert outcome.raw_objects == []
    assert outcome.bronze_objects == []
    assert outcome.skipped is None
