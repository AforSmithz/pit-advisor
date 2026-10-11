from datetime import date
from types import SimpleNamespace

import pytest

from pitadvisor.config import Settings
from pitadvisor.handlers import watch as handler
from pitadvisor.ingest.raw_store import write_bronze
from pitadvisor.quality.contracts import RaceRow
from pitadvisor.types import EventKey

SETTINGS = Settings(
    weekend_machine_arn="arn:weekend",
    light_machine_arn="arn:light",
    dispatch_parameter="/pitadvisor/dev/github-dispatch",
    github_repo="owner/repo",
)


class MissingError(Exception):
    pass


class Ssm:
    exceptions = SimpleNamespace(ParameterNotFound=MissingError)

    def __init__(self, token):
        self.token = token

    def get_parameter(self, Name, WithDecryption):
        assert WithDecryption
        if self.token is None:
            raise MissingError(Name)
        return {"Parameter": {"Value": self.token}}


class States:
    def __init__(self, running=()):
        self.running = set(running)
        self.started = []

    def list_executions(self, stateMachineArn, statusFilter, maxResults):
        return {"executions": [{}] if stateMachineArn in self.running else []}

    def start_execution(self, **kwargs):
        self.started.append(kwargs)


def session(clients):
    return SimpleNamespace(client=lambda name, region_name=None: clients[name])


def test_publish_without_a_token_does_not_fail_the_run(monkeypatch):
    monkeypatch.setattr(handler, "boto_session", lambda settings: session({"ssm": Ssm(None)}))
    assert handler.publish(SETTINGS) == {"published": False}


def test_publish_dispatches_with_the_stored_token(monkeypatch):
    sent = []
    monkeypatch.setattr(handler, "boto_session", lambda settings: session({"ssm": Ssm("t-1")}))
    monkeypatch.setattr(
        handler.watch, "dispatch", lambda repo, token: sent.append((repo, token)) or 204
    )
    assert handler.publish(SETTINGS) == {"published": True, "status": 204}
    assert sent == [("owner/repo", "t-1")]


def test_poll_without_a_calendar_watches_nothing(monkeypatch, store):
    monkeypatch.setattr(handler, "object_store", lambda settings: store)
    monkeypatch.setattr(handler, "boto_session", lambda settings: session({}))
    assert handler.poll(SETTINGS) == {"watching": False}


@pytest.fixture
def calendar(store):
    row = RaceRow(
        run_id="run-1",
        ingested_at="2026-10-01T00:00:00Z",
        season=2026,
        round=17,
        race_name="Singapore Grand Prix",
        circuit_id="marina_bay",
        circuit_name="Marina Bay",
        latitude=1.0,
        longitude=2.0,
        race_date=date(2026, 10, 11),
    )
    write_bronze(store, "races", EventKey(season=2026, round=17), [row])
    return store


def test_poll_hands_the_calendar_and_machines_to_the_watch(monkeypatch, calendar):
    states = States(running={"arn:light"})
    seen = {}

    def fake_poll(now, races, store, probes, running, start):
        seen["races"] = races
        seen["running"] = running()
        start(handler.watch.Action(mode="stewards", season=2026, round=17, reason="r"))
        start(handler.watch.Action(mode="full", season=2026, round=17, reason="r"))
        return None

    monkeypatch.setattr(handler, "object_store", lambda settings: calendar)
    monkeypatch.setattr(
        handler,
        "boto_session",
        lambda settings: session({"stepfunctions": states, "dynamodb": object()}),
    )
    monkeypatch.setattr(handler.watch, "poll", fake_poll)
    assert handler.poll(SETTINGS) == {"action": None}
    assert seen["races"][0]["round"] == 17
    assert seen["running"] is True
    assert [item["stateMachineArn"] for item in states.started] == ["arn:light", "arn:weekend"]
    assert states.started[1]["input"] == '{"force": true}'


def test_the_handler_routes_publish(monkeypatch):
    monkeypatch.setattr(handler, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(handler, "publish", lambda settings: {"published": False})
    monkeypatch.setattr(handler, "poll", lambda settings: {"action": None})
    context = SimpleNamespace(
        function_name="watch",
        memory_limit_in_mb=512,
        invoked_function_arn="arn:fn",
        aws_request_id="r",
    )
    assert handler.handler({"action": "publish"}, context) == {"published": False}
    assert handler.handler({}, context) == {"action": None}
