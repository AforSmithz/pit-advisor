import json
from datetime import UTC, date, datetime, timedelta

import pytest

from pitadvisor.ingest import http
from pitadvisor.ingest.watch import (
    STATE_KEY,
    Action,
    Probes,
    Session,
    Unrecorded,
    WatchState,
    Weekend,
    dispatch,
    due,
    execution,
    live_probes,
    load,
    poll,
    record,
    sessions,
    watched,
)

WEEKEND = Weekend(
    season=2026, round=17, race_name="Singapore Grand Prix", race_date=date(2026, 10, 11)
)


def listed(key, meeting, name, start, end, cancelled=False):
    return {
        "session_key": key,
        "meeting_key": meeting,
        "session_name": name,
        "date_start": start,
        "date_end": end,
        "is_cancelled": cancelled,
    }


LISTING = [
    listed(11100, 1280, "Race", "2026-05-03T20:00:00+00:00", "2026-05-03T22:00:00+00:00"),
    listed(11380, 1296, "Practice 1", "2026-10-09T08:30:00+00:00", "2026-10-09T09:30:00+00:00"),
    listed(
        11382, 1296, "Sprint Qualifying", "2026-10-09T12:30:00+00:00", "2026-10-09T13:14:00+00:00"
    ),
    listed(11384, 1296, "Sprint", "2026-10-10T09:00:00+00:00", "2026-10-10T10:00:00+00:00"),
    listed(11386, 1296, "Qualifying", "2026-10-10T13:30:00+00:00", "2026-10-10T14:30:00+00:00"),
    listed(11388, 1296, "Race", "2026-10-11T12:00:00+00:00", "2026-10-11T14:00:00+00:00"),
    # las vegas starts saturday night local, which is sunday in utc
    listed(11420, 1300, "Race", "2026-11-22T04:00:00+00:00", "2026-11-22T06:00:00+00:00"),
]

CLOCK = sessions(LISTING, WEEKEND.race_date)
AT = {session.kind: session for session in CLOCK}


def probes(archived=(), classified=(), documents=0):
    return Probes(
        sessions=lambda weekend: CLOCK,
        archived=lambda session: session.kind in archived,
        classified=lambda weekend, resource: resource in classified,
        documents=lambda weekend, state: documents,
    )


def test_the_session_clock_is_read_in_utc_and_skips_practice():
    assert [session.kind for session in CLOCK] == [
        "sprint_qualifying",
        "sprint",
        "qualifying",
        "race",
    ]
    assert AT["race"].starts_at == datetime(2026, 10, 11, 12, tzinfo=UTC)
    assert AT["race"].ends_at == datetime(2026, 10, 11, 14, tzinfo=UTC)


def test_a_night_race_past_midnight_utc_still_finds_its_weekend():
    vegas = sessions(LISTING, date(2026, 11, 21))
    assert [session.session_key for session in vegas] == [11420]


def test_a_race_openf1_does_not_list_has_no_clock():
    assert sessions(LISTING, date(2026, 10, 25)) == []


def test_a_cancelled_session_is_not_on_the_clock():
    cancelled = [*LISTING[:-2], {**LISTING[-2], "is_cancelled": True}]
    assert "race" not in {session.kind for session in sessions(cancelled, WEEKEND.race_date)}


RACES = [
    {
        "season": 2026,
        "round": 16,
        "race_name": "Malaysian Grand Prix",
        "race_date": date(2026, 10, 4),
    },
    {
        "season": 2026,
        "round": 17,
        "race_name": "Singapore Grand Prix",
        "race_date": date(2026, 10, 11),
    },
    {
        "season": 2026,
        "round": 18,
        "race_name": "United States Grand Prix",
        "race_date": date(2026, 10, 25),
    },
]


def test_the_weekend_is_watched_from_thursday_to_tuesday():
    assert watched(RACES, date(2026, 10, 7)) is not None
    assert watched(RACES, date(2026, 10, 13)).round == 17
    assert watched(RACES, date(2026, 10, 18)) is None


def test_the_nearer_race_wins_when_two_windows_touch():
    assert watched(RACES, date(2026, 10, 6)).round == 16
    assert watched(RACES, date(2026, 10, 8)).round == 17


def test_nothing_is_due_before_a_session_ends():
    assert due(AT["race"].starts_at, WEEKEND, WatchState(), probes()) is None


def test_qualifying_is_due_once_jolpica_has_the_classification():
    after = AT["qualifying"].ends_at + timedelta(minutes=20)
    assert due(after, WEEKEND, WatchState(), probes()) is None
    action = due(after, WEEKEND, WatchState(), probes(classified={"qualifying"}))
    assert action == Action(
        mode="full", season=2026, round=17, reason="qualifying is in", mark="2026-17-qualifying"
    )


def test_the_race_waits_for_both_the_timing_archive_and_the_result():
    after = AT["race"].ends_at + timedelta(minutes=40)
    handled = WatchState(
        handled=["2026-17-sprint_qualifying", "2026-17-sprint", "2026-17-qualifying"]
    )
    assert due(after, WEEKEND, handled, probes(classified={"results"})) is None
    assert due(after, WEEKEND, handled, probes(archived={"race"})) is None
    both = due(after, WEEKEND, handled, probes(archived={"race"}, classified={"results"}))
    assert both is not None
    assert both.mark == "2026-17-race"


def test_sprint_qualifying_needs_only_the_archive():
    after = AT["sprint_qualifying"].ends_at + timedelta(minutes=30)
    action = due(after, WEEKEND, WatchState(), probes(archived={"sprint_qualifying"}))
    assert action is not None
    assert action.mark == "2026-17-sprint_qualifying"


def test_a_handled_session_is_not_run_twice():
    after = AT["qualifying"].ends_at + timedelta(minutes=20)
    state = WatchState(
        handled=["2026-17-sprint_qualifying", "2026-17-sprint", "2026-17-qualifying"]
    )
    assert due(after, WEEKEND, state, probes(classified={"qualifying"})) is None


def test_a_session_that_never_lands_stops_being_waited_on():
    late = AT["sprint_qualifying"].ends_at + timedelta(hours=40)
    state = WatchState(handled=["2026-17-sprint", "2026-17-qualifying", "2026-17-race"])
    assert due(late, WEEKEND, state, probes(archived={"sprint_qualifying"})) is None


def test_a_new_stewards_document_is_a_light_run():
    quiet = AT["sprint_qualifying"].starts_at - timedelta(hours=5)
    action = due(quiet, WEEKEND, WatchState(), probes(documents=2))
    assert action is not None
    assert action.mode == "stewards"
    assert action.mark is None


def test_a_session_result_comes_before_a_stewards_document():
    after = AT["qualifying"].ends_at + timedelta(minutes=20)
    state = WatchState(handled=["2026-17-sprint_qualifying", "2026-17-sprint"])
    action = due(after, WEEKEND, state, probes(classified={"qualifying"}, documents=3))
    assert action is not None
    assert action.mode == "full"


def test_the_weather_is_snapshotted_two_hours_before_the_race_once():
    before = AT["race"].starts_at - timedelta(hours=2)
    state = WatchState(handled=[f"2026-17-{kind}" for kind in AT])
    action = due(before, WEEKEND, state, probes())
    assert action is not None
    assert action.mode == "snapshot"
    assert action.mark == "2026-17-race"
    assert due(before, WEEKEND, record(state, action), probes()) is None


def test_sprint_qualifying_gets_no_snapshot():
    before = AT["sprint_qualifying"].starts_at - timedelta(hours=2)
    assert due(before, WEEKEND, WatchState(), probes()) is None


def test_record_keeps_each_mark_in_its_own_list():
    full = Action(mode="full", season=2026, round=17, reason="r", mark="2026-17-race")
    light = Action(mode="snapshot", season=2026, round=17, reason="r", mark="2026-17-race")
    stewards = Action(mode="stewards", season=2026, round=17, reason="r")
    state = record(record(record(WatchState(), full), light), stewards)
    assert state.handled == ["2026-17-race"]
    assert state.snapshots == ["2026-17-race"]


def test_a_full_run_forces_the_weekend_machine_and_a_light_one_names_its_mode():
    now = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
    name, payload = execution(Action(mode="full", season=2026, round=17, reason="r"), now)
    assert payload == {"force": True}
    assert name == "watch-full-2026-17-20261011T150000"
    _, light = execution(Action(mode="stewards", season=2026, round=17, reason="r"), now)
    assert light == {"mode": "stewards", "season": 2026, "round": 17}


def test_poll_starts_the_action_and_remembers_it(store):
    started: list[Action] = []
    after = AT["qualifying"].ends_at + timedelta(minutes=20)
    state = WatchState(handled=["2026-17-sprint_qualifying", "2026-17-sprint"])
    store.put(STATE_KEY, state.model_dump_json().encode())
    action = poll(
        after, RACES, store, probes(classified={"qualifying"}), lambda: False, started.append
    )
    assert started == [action]
    assert "2026-17-qualifying" in load(store).handled


def test_poll_waits_while_a_run_is_going(store):
    started: list[Action] = []
    after = AT["qualifying"].ends_at + timedelta(minutes=20)
    assert (
        poll(after, RACES, store, probes(classified={"qualifying"}), lambda: True, started.append)
        is None
    )
    assert started == []
    assert not store.exists(STATE_KEY)


def test_poll_outside_a_weekend_asks_nothing(store):
    def boom(*_):
        raise AssertionError("probed outside a weekend")

    quiet = datetime(2026, 10, 18, 12, tzinfo=UTC)
    loud = Probes(sessions=boom, archived=boom, classified=boom, documents=boom)
    assert poll(quiet, RACES, store, loud, lambda: False, boom) is None


def test_a_probe_never_writes_to_the_ledger():
    ledger = Unrecorded()
    assert ledger.lookup("https://example.com") is None
    assert ledger.record(None) is None  # type: ignore[arg-type]


class Limiter:
    def acquire(self):
        return None


def fake_fetch(pages: dict[str, object], seen: list[str]):
    def fetch(url, ledger, limiter, accept="application/json"):
        seen.append(url)
        assert isinstance(ledger, Unrecorded)
        for prefix, body in pages.items():
            if url.startswith(prefix):
                text = body if isinstance(body, str) else "﻿" + json.dumps(body)
                return http.Response(
                    url=url, status=200, body=text.encode(), fetched_at=datetime.now(UTC)
                )
        raise http.FetchError(url, 404, "not found")

    return fetch


def test_live_probes_read_openf1_and_jolpica(store):
    seen: list[str] = []
    pages = {
        "https://api.openf1.org/v1/sessions?year=2026": LISTING,
        "https://api.openf1.org/v1/race_control?session_key=11388": [
            {"category": "Flag", "flag": "CHEQUERED", "message": "CHEQUERED FLAG"}
        ],
        "https://api.openf1.org/v1/race_control?session_key=11382": [
            {"category": "Flag", "flag": "GREEN", "message": "GREEN LIGHT - PIT EXIT OPEN"}
        ],
        "https://api.jolpi.ca/ergast/f1/2026/17/results.json": {
            "MRData": {"RaceTable": {"Races": [{"Results": [{"position": "1"}]}]}}
        },
        "https://api.jolpi.ca/ergast/f1/2026/17/qualifying.json": {
            "MRData": {"RaceTable": {"Races": []}}
        },
    }
    live = live_probes(store, Limiter(), Limiter(), Limiter(), fake_fetch(pages, seen))
    clock = live.sessions(WEEKEND)
    assert [session.kind for session in clock] == [
        "sprint_qualifying",
        "sprint",
        "qualifying",
        "race",
    ]
    assert live.archived(clock[-1])
    assert not live.archived(clock[0])
    assert live.classified(WEEKEND, "results")
    assert not live.classified(WEEKEND, "qualifying")


FIA = "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14"
CHAMPIONSHIP_HTML = (
    '<option value="/documents/championships/fia-formula-one-world-championship-14/season/'
    'season-2026-2072">2026</option>'
)
SEASON_HTML = (
    '<option value="/documents/championships/fia-formula-one-world-championship-14/season/'
    'season-2026-2072/event/Singapore Grand Prix">Singapore</option>'
)
EVENT_HTML = (
    '<a href="/sites/default/files/decision-doc-20.pdf">Doc 20 - Infringement - Car 63 - '
    "Power Unit elements Published on 09.10.26 19:40</a>"
    '<a href="/sites/default/files/timetable.pdf">Doc 1 - Event Timetable</a>'
)


def test_the_stewards_probe_counts_documents_not_yet_in_raw(store):
    seen: list[str] = []
    pages = {
        f"{FIA}/season/season-2026-2072/event/": EVENT_HTML,
        f"{FIA}/season/": SEASON_HTML,
        FIA: CHAMPIONSHIP_HTML,
    }
    waits: list[float] = []
    live = live_probes(
        store, Limiter(), Limiter(), Limiter(), fake_fetch(pages, seen), waits.append, lambda: 0.0
    )
    state = WatchState()
    assert live.documents(WEEKEND, state) == 1
    assert state.fia_paths["2026-17"].endswith("/event/Singapore Grand Prix")
    assert seen[-1].endswith("/event/Singapore%20Grand%20Prix")
    assert len(seen) == 3
    # the crawl delay holds between the requests of one look
    assert waits
    assert all(wait == pytest.approx(10.0) for wait in waits)
    seen.clear()
    assert live.documents(WEEKEND, state) == 1
    assert len(seen) == 1


class Answer:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_dispatch_asks_github_for_the_publish_workflow():
    sent = []

    def opener(request, timeout):
        sent.append(request)
        return Answer()

    assert dispatch("owner/repo", "token-1", opener) == 204
    request = sent[0]
    assert request.full_url == (
        "https://api.github.com/repos/owner/repo/actions/workflows/publish.yml/dispatches"
    )
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"ref": "main"}
    assert request.headers["Authorization"] == "Bearer token-1"


def test_session_model_round_trips():
    session = Session(
        kind="race",
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        ends_at=datetime(2026, 1, 1, 2, tzinfo=UTC),
        session_key=11388,
    )
    assert Session.model_validate_json(session.model_dump_json()) == session


def test_one_full_run_settles_every_session_already_in():
    # the watch went live two days into the weekend: one run ingests all three
    late = AT["race"].ends_at + timedelta(hours=4)
    ready = probes(archived={"sprint", "race"}, classified={"sprint", "qualifying", "results"})
    action = due(late, WEEKEND, WatchState(), ready)
    assert action is not None
    assert action.mark == "2026-17-sprint"
    assert action.also == ["2026-17-qualifying", "2026-17-race"]
    assert due(late, WEEKEND, record(WatchState(), action), ready) is None
