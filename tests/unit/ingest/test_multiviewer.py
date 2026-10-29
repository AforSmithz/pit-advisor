import http.client
import io
import json
import urllib.error

import pytest

from pitadvisor.ingest import multiviewer
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Source

KEY = EventKey(season=2025, round=20)
BODY = json.dumps(
    {
        "corners": [
            {"number": 1, "angle": 27.0, "trackPosition": {"x": 11549.4, "y": -620.4}},
            {"number": 2, "angle": -161.5, "trackPosition": {"x": 11579.6, "y": -1334.7}},
        ],
        "x": [-295, -218],
        "y": [10, 12],
    }
).encode()


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.targets: list[str] = []

    def __call__(self, request, timeout):
        self.targets.append(request.full_url)
        assert request.get_header("User-agent")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(answer)


def failure(code):
    return urllib.error.HTTPError("https://api.multiviewer.app", code, "no", {}, None)


def test_corners_come_back_in_metres_in_the_openf1_frame():
    found = multiviewer.corners(BODY)
    assert [(c.number, c.x, c.y) for c in found] == [
        (1, pytest.approx(1154.94), pytest.approx(-62.04)),
        (2, pytest.approx(1157.96), pytest.approx(-133.47)),
    ]


def test_a_map_without_corners_has_none():
    assert multiviewer.corners(b"{}") == []


def test_the_map_is_asked_for_by_circuit_and_season_then_held_in_raw(store):
    raw = RawStore(store)
    opener = Scripted(BODY)
    assert multiviewer.fetch(raw, KEY, 65, "run-1", opener, sleep=lambda _: None) == BODY
    assert opener.targets == ["https://api.multiviewer.app/api/v1/circuits/65/2025"]
    again = multiviewer.fetch(raw, KEY, 65, "run-2", Scripted(), sleep=lambda _: None)
    assert again == BODY
    assert raw.versions(Source.MULTIVIEWER, KEY, "circuit-65")


def test_a_server_error_is_retried_and_a_missing_map_is_not():
    waits: list[float] = []
    assert (
        multiviewer.call(
            multiviewer.circuit_url(65, 2025), Scripted(failure(502), BODY), waits.append
        )
        == BODY
    )
    assert waits == [5.0]
    with pytest.raises(multiviewer.MultiViewerError):
        multiviewer.call(multiviewer.circuit_url(65, 2025), Scripted(failure(404)), waits.append)


def test_a_cut_off_body_is_retried():
    waits: list[float] = []
    cut = http.client.IncompleteRead(b"{")
    assert (
        multiviewer.call(multiviewer.circuit_url(9, 2025), Scripted(cut, BODY), waits.append)
        == BODY
    )
