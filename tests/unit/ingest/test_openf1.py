import json
from datetime import UTC, date, datetime

import polars as pl
import pytest

from pitadvisor.ingest import http
from pitadvisor.ingest.openf1 import (
    PARTS,
    OpenF1Client,
    backfill,
    finished,
    ingest_session,
    scheduled,
    status_during,
    to_records,
    track_timeline,
    url,
)
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.ingest.rebuild import rebuild_bronze
from pitadvisor.quality.lineage import raw_count
from pitadvisor.types import SessionKey, SessionKind, Source

KEY = SessionKey(season=2026, round=18, session=SessionKind.RACE)
BRONZE = "bronze/table=session_laps/season=2026/round=18/session=race/session_laps.parquet"
RACES = {17: date(2026, 10, 11), 18: date(2026, 10, 25)}


def at(clock: str) -> str:
    return f"2026-10-25T{clock}+00:00"


def lap(car, number, start, seconds, out=False, sectors=None):
    first, second, third = sectors or (seconds / 3, seconds / 3, seconds / 3)
    return {
        "driver_number": car,
        "lap_number": number,
        "date_start": at(start),
        "lap_duration": seconds,
        "duration_sector_1": first,
        "duration_sector_2": second,
        "duration_sector_3": third,
        "is_pit_out_lap": out,
    }


def bundle(**overrides):
    parts = {
        "session": [{"session_key": 11396, "date_start": at("20:00:00")}],
        "drivers": [
            {"driver_number": 1, "name_acronym": "NOR"},
            {"driver_number": 44, "name_acronym": "HAM"},
        ],
        "laps": [
            lap(1, 1, "20:03:00", 100.0),
            lap(1, 2, "20:04:40", 95.0),
            lap(1, 3, "20:06:15", 96.0),
            lap(1, 4, "20:07:51", 110.0),
            lap(1, 5, "20:09:41", 94.0, out=True),
            lap(44, 1, "20:03:01", 100.5),
            lap(44, 2, "20:04:42", 94.0),
        ],
        "stints": [
            {
                "driver_number": 1,
                "stint_number": 1,
                "lap_start": 1,
                "lap_end": 4,
                "compound": "MEDIUM",
                "tyre_age_at_start": 2,
            },
            {
                "driver_number": 1,
                "stint_number": 2,
                "lap_start": 5,
                "lap_end": 5,
                "compound": "HARD",
                "tyre_age_at_start": 0,
            },
        ],
        "pit": [{"driver_number": 1, "lap_number": 4}],
        "race_control": [
            {"date": at("20:00:00"), "category": "Flag", "flag": "GREEN", "scope": "Track"},
            {
                "date": at("20:05:00"),
                "category": "Other",
                "message": "CAR 1 (NOR) TIME 1:35.000 DELETED - TRACK LIMITS AT TURN 2 LAP 2 20:05",
            },
            {"date": at("20:12:00"), "category": "Flag", "flag": "CHEQUERED", "scope": "Track"},
        ],
        "position": [
            {"driver_number": 1, "date": at("20:02:00"), "position": 2},
            {"driver_number": 1, "date": at("20:04:00"), "position": 1},
            {"driver_number": 44, "date": at("20:02:00"), "position": 1},
            {"driver_number": 44, "date": at("20:04:01"), "position": 2},
        ],
    }
    parts.update(overrides)
    return parts


def stamped():
    return {"run_id": "run-1", "ingested_at": datetime(2026, 10, 26, tzinfo=UTC)}


def by_lap(records, code="NOR"):
    return {row["lap"]: row for row in records if row["driver_code"] == code}


def test_laps_carry_the_driver_code_and_millis():
    first = by_lap(to_records(bundle(), KEY, stamped()))[1]
    assert first["driver_code"] == "NOR"
    assert first["lap_time_millis"] == 100000
    assert first["sector1_millis"] == 33333


def test_the_stint_gives_compound_tyre_life_and_lap_in_stint():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert (laps[3]["stint"], laps[3]["compound"], laps[3]["lap_in_stint"]) == (1, "MEDIUM", 3)
    # two laps old when fitted, so the third lap on it is its fifth
    assert laps[3]["tyre_life"] == 5
    assert (laps[5]["stint"], laps[5]["compound"], laps[5]["tyre_life"]) == (2, "HARD", 1)


def test_the_pit_lap_is_the_in_lap_and_the_next_is_the_out_lap():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[4]["pit_in"]
    assert not laps[4]["pit_out"]
    assert laps[5]["pit_out"]
    assert not laps[5]["pit_in"]
    assert not laps[4]["is_accurate"]
    assert not laps[5]["is_accurate"]


def test_the_first_lap_is_never_accurate_and_a_clean_flying_lap_is():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert not laps[1]["is_accurate"]
    assert laps[3]["is_accurate"]


def test_sectors_that_do_not_add_up_make_a_lap_inaccurate():
    odd = bundle(laps=[lap(1, 2, "20:04:40", 95.0, sectors=(30.0, 30.0, 30.0))])
    assert not to_records(odd, KEY, stamped())[0]["is_accurate"]


def test_a_track_limits_deletion_lands_on_the_lap_it_names():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[2]["is_deleted"]
    assert not laps[3]["is_deleted"]


def test_a_reinstated_time_is_not_deleted():
    control = [
        *bundle()["race_control"],
        {
            "date": at("20:08:00"),
            "category": "Other",
            "message": "CAR 1 (NOR) TIME 1:35.000 REINSTATED",
        },
    ]
    laps = by_lap(to_records(bundle(race_control=control), KEY, stamped()))
    assert not laps[2]["is_deleted"]


def test_a_deleted_lap_is_never_a_personal_best():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[1]["is_personal_best"]
    assert not laps[2]["is_personal_best"]
    assert laps[3]["is_personal_best"]


def test_the_running_order_is_where_the_car_was_at_the_line():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[1]["position"] == 1
    assert by_lap(to_records(bundle(), KEY, stamped()), "HAM")[1]["position"] == 2


def test_only_a_race_or_sprint_has_a_running_order():
    practice = SessionKey(season=2026, round=18, session=SessionKind.FP2)
    assert all(row["position"] is None for row in to_records(bundle(), practice, stamped()))


def test_the_session_clock_is_the_lap_end_from_the_session_start():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[1]["session_time_millis"] == (3 * 60 + 100) * 1000


def test_a_green_lap_reads_as_fastf1_writes_it():
    laps = by_lap(to_records(bundle(), KEY, stamped()))
    assert laps[3]["track_status"] == "1"


def test_a_safety_car_runs_until_the_leader_starts_the_lap_after_it_comes_in():
    control = [
        {"date": at("20:00:00"), "category": "Flag", "flag": "GREEN", "scope": "Track"},
        {"date": at("20:05:00"), "category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"},
        {
            "date": at("20:07:00"),
            "category": "SafetyCar",
            "message": "SAFETY CAR IN THIS LAP",
            "lap_number": 3,
        },
        {"date": at("20:12:00"), "category": "Flag", "flag": "CHEQUERED", "scope": "Track"},
    ]
    laps = by_lap(to_records(bundle(race_control=control), KEY, stamped()))
    assert laps[2]["track_status"] == "14"
    assert laps[3]["track_status"] == "4"
    assert laps[4]["track_status"] == "1"


def test_a_virtual_safety_car_and_its_ending_have_their_own_digits():
    control = [
        {"date": at("20:06:30"), "category": "SafetyCar", "message": "VSC DEPLOYED"},
        {"date": at("20:07:10"), "category": "SafetyCar", "message": "VSC ENDING"},
        {"date": at("20:07:30"), "category": "Flag", "flag": "CLEAR", "scope": "Track"},
    ]
    laps = by_lap(to_records(bundle(race_control=control), KEY, stamped()))
    assert laps[3]["track_status"] == "1671"


def test_a_sector_yellow_shows_until_that_sector_is_clear():
    control = [
        {
            "date": at("20:05:00"),
            "category": "Flag",
            "flag": "YELLOW",
            "scope": "Sector",
            "sector": 5,
        },
        {
            "date": at("20:05:10"),
            "category": "Flag",
            "flag": "YELLOW",
            "scope": "Sector",
            "sector": 6,
        },
        {
            "date": at("20:05:20"),
            "category": "Flag",
            "flag": "CLEAR",
            "scope": "Sector",
            "sector": 5,
        },
        {
            "date": at("20:05:30"),
            "category": "Flag",
            "flag": "CLEAR",
            "scope": "Sector",
            "sector": 6,
        },
    ]
    timeline = track_timeline(control, {})
    assert [code for _, code in timeline] == ["2", "1"]
    assert timeline[1][0] == datetime(2026, 10, 25, 20, 5, 30, tzinfo=UTC)


def test_a_red_flag_is_a_five():
    control = [{"date": at("20:05:00"), "category": "Flag", "flag": "RED", "scope": "Track"}]
    start = datetime(2026, 10, 25, 20, 4, tzinfo=UTC)
    end = datetime(2026, 10, 25, 20, 6, tzinfo=UTC)
    assert status_during(track_timeline(control, {}), start, end) == "15"


def test_an_unknown_compound_is_no_compound():
    stints = [{**bundle()["stints"][0], "compound": "UNKNOWN"}]
    assert by_lap(to_records(bundle(stints=stints), KEY, stamped()))[1]["compound"] is None


def test_a_session_is_finished_once_the_chequered_flag_shows():
    assert finished(bundle()["race_control"])
    assert finished([{"message": "SESSION FINISHED"}])
    assert not finished(bundle()["race_control"][:2])


LISTING = [
    {
        "session_key": 11388,
        "meeting_key": 1296,
        "session_name": "Race",
        "date_start": "2026-10-11T12:00:00+00:00",
        "date_end": "2026-10-11T14:00:00+00:00",
    },
    {
        "session_key": 11392,
        "meeting_key": 1297,
        "session_name": "Qualifying",
        "date_start": "2026-10-24T21:00:00+00:00",
        "date_end": "2026-10-24T22:00:00+00:00",
    },
    {
        "session_key": 11396,
        "meeting_key": 1297,
        "session_name": "Race",
        "date_start": "2026-10-25T20:00:00+00:00",
        "date_end": "2026-10-25T22:00:00+00:00",
    },
    {
        "session_key": 11420,
        "meeting_key": 1300,
        "session_name": "Race",
        "date_start": "2026-11-22T04:00:00+00:00",
        "date_end": "2026-11-22T06:00:00+00:00",
    },
]


def test_meetings_become_rounds_through_the_race_day():
    found = scheduled(LISTING, RACES, (SessionKind.RACE, SessionKind.QUALIFYING))
    assert [(round_, kind, item["session_key"]) for round_, kind, item in found] == [
        (17, SessionKind.RACE, 11388),
        (18, SessionKind.QUALIFYING, 11392),
        (18, SessionKind.RACE, 11396),
    ]


def test_a_night_race_is_found_a_day_off_in_utc():
    found = scheduled(LISTING, {22: date(2026, 11, 21)}, (SessionKind.RACE,))
    assert [(round_, item["session_key"]) for round_, _, item in found] == [(22, 11420)]


def test_a_cancelled_race_has_no_round():
    cancelled = [{**item, "is_cancelled": True} for item in LISTING]
    assert scheduled(cancelled, RACES, (SessionKind.RACE,)) == []


OPENF1_ENDPOINTS = {"sessions", "drivers", "laps", "stints", "pit", "race_control", "position"}


class Upstream:
    def __init__(self, parts=None, listing=LISTING):
        self.parts = parts or bundle()
        self.listing = listing
        self.seen: list[str] = []

    def __call__(self, target, ledger, limiter):
        self.seen.append(target)
        assert isinstance(ledger, http.Unconditional)
        if target.startswith(url("sessions", year=2026)):
            body = self.listing
        else:
            name = target.split("/v1/", 1)[1].split("?", 1)[0]
            if name not in OPENF1_ENDPOINTS:
                raise http.FetchError(target, 404, "not found")
            body = self.parts["session" if name == "sessions" else name]
        return http.Response(
            url=target, status=200, body=json.dumps(body).encode(), fetched_at=datetime.now(UTC)
        )


@pytest.fixture
def pauses():
    return []


def client_for(store, upstream, pauses):
    return OpenF1Client(RawStore(store), None, None, "run-1", fetch=upstream, sleep=pauses.append)


def test_a_finished_session_lands_every_part_then_bronze(store, pauses):
    upstream = Upstream()
    outcome = ingest_session(client_for(store, upstream, pauses), store, KEY, 11396)
    assert outcome.rows == 7
    assert outcome.requests == len(PARTS)
    assert len(outcome.raw_objects) == len(PARTS)
    assert all(
        "raw/source=openf1/season=2026/round=18/session=race/" in key for key in outcome.raw_objects
    )
    assert store.exists(BRONZE)
    # one request every two seconds is the free tier's thirty a minute
    assert pauses == [2.0] * (len(PARTS) - 1)


def test_an_unfinished_session_lands_nothing(store, pauses):
    parts = bundle(race_control=bundle()["race_control"][:1])
    outcome = ingest_session(client_for(store, Upstream(parts), pauses), store, KEY, 11396)
    assert outcome.skipped == "race not finished upstream"
    assert outcome.requests == 1
    assert not list(store.list("raw/"))
    assert not store.exists(BRONZE)


def test_a_session_without_laps_is_skipped(store, pauses):
    outcome = ingest_session(
        client_for(store, Upstream(bundle(laps=[])), pauses), store, KEY, 11396
    )
    assert outcome.skipped == "race has no laps upstream"
    assert not store.exists(BRONZE)


def test_backfill_takes_ended_sessions_and_skips_what_bronze_has(store, pauses):
    upstream = Upstream()
    now = datetime(2026, 10, 26, tzinfo=UTC)
    client = client_for(store, upstream, pauses)
    first = backfill(client, store, 2026, RACES, (SessionKind.RACE,), now=now)
    assert [(o.round, o.rows) for o in first] == [(17, 7), (18, 7)]
    again = backfill(client, store, 2026, RACES, (SessionKind.RACE,), now=now)
    assert [o.skipped for o in again] == ["already in bronze", "already in bronze"]


def test_backfill_leaves_a_session_still_ahead(store, pauses):
    during = datetime(2026, 10, 25, 21, tzinfo=UTC)
    found = backfill(
        client_for(store, Upstream(), pauses), store, 2026, RACES, (SessionKind.RACE,), now=during
    )
    assert [o.round for o in found] == [17]


def test_backfill_without_a_calendar_asks_nothing(store, pauses):
    upstream = Upstream()
    assert backfill(client_for(store, upstream, pauses), store, 2026, {}) == []
    assert upstream.seen == []


def test_the_season_listing_lands_in_raw_too(store, pauses):
    client_for(store, Upstream(), pauses).sessions(2026)
    assert [item.key for item in store.list("raw/source=openf1/season=2026/sessions-")]


def test_bronze_is_rebuildable_from_the_openf1_raw(store, pauses):
    ingest_session(client_for(store, Upstream(), pauses), store, KEY, 11396)
    before = pl.read_parquet(store.get(BRONZE))
    store.clear("bronze/")
    rebuilt = rebuild_bronze(store, "run-2", source=Source.OPENF1)
    assert [(o.source, o.round, o.rows) for o in rebuilt] == [(Source.OPENF1, 18, 7)]
    after = pl.read_parquet(store.get(BRONZE))
    columns = [name for name in before.columns if name not in ("run_id", "ingested_at")]
    assert before.select(columns).equals(after.select(columns))


def test_lineage_counts_the_openf1_laps_as_session_laps(store, pauses):
    ingest_session(client_for(store, Upstream(), pauses), store, KEY, 11396)
    assert raw_count(store, "session_laps") == 1


def test_a_stint_starts_on_the_out_lap_even_when_openf1_starts_it_on_the_in_lap():
    early = [
        {**bundle()["stints"][0], "lap_end": 3},
        {**bundle()["stints"][1], "lap_start": 4},
    ]
    laps = by_lap(to_records(bundle(stints=early), KEY, stamped()))
    assert (laps[4]["stint"], laps[4]["lap_in_stint"], laps[4]["compound"]) == (1, 4, "MEDIUM")
    assert (laps[5]["stint"], laps[5]["lap_in_stint"], laps[5]["compound"]) == (2, 1, "HARD")


def test_a_lap_with_no_published_stint_falls_back_to_the_stint_number():
    stints = [{**bundle()["stints"][0], "lap_start": 9, "lap_end": 9}]
    assert by_lap(to_records(bundle(stints=stints), KEY, stamped()))[1]["compound"] == "MEDIUM"


def test_a_deletion_is_matched_by_its_time_when_the_lap_count_disagrees():
    control = [
        {
            "date": at("20:08:00"),
            "category": "Other",
            "message": "CAR 1 (NOR) TIME 1:36.000 DELETED - TRACK LIMITS AT TURN 2 LAP 4 20:07",
        }
    ]
    laps = by_lap(to_records(bundle(race_control=control), KEY, stamped()))
    assert laps[3]["is_deleted"]
    assert not laps[4]["is_deleted"]


def test_a_lap_deleted_without_a_time_falls_back_to_its_lap():
    control = [
        {
            "date": at("20:08:00"),
            "category": "Other",
            "message": "CAR 1 (NOR) LAP DELETED - TRACK LIMITS AT TURN 2 LAP 4 20:07 (PIT)",
        }
    ]
    assert by_lap(to_records(bundle(race_control=control), KEY, stamped()))[4]["is_deleted"]
