import io
import urllib.error

import pytest

from pitadvisor.ingest import osm
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Source

KEY = EventKey(season=2025, round=20)
JSON = b'{"elements": []}'


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def failure(code):
    return urllib.error.HTTPError("https://overpass-api.de", code, "busy", {}, None)


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        assert request.get_header("User-agent")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(answer)


def test_the_area_query_asks_for_every_feature_inside_the_box():
    query = osm.area_query(19.39, -99.10, 19.41, -99.07)
    assert query.count("(19.39000,-99.10000,19.41000,-99.07000);") == len(osm.AREA_FEATURES)
    assert 'node["natural"="tree"]' in query
    assert query.startswith("[out:json]")
    assert query.endswith("out geom;")


def test_the_raceway_query_is_a_circle_around_the_circuit():
    assert "(around:2500,19.404200,-99.090700)" in osm.raceway_query(19.4042, -99.0907, 2500)


def test_the_raceway_query_also_takes_circuit_relations_for_street_tracks():
    query = osm.raceway_query(36.1147, -115.173, 4000)
    assert 'rel["type"="circuit"](around:4000,36.114700,-115.173000)' in query
    assert query.endswith("out geom;")


def test_a_busy_server_is_retried_with_a_growing_wait():
    waits = []
    opener = Scripted(failure(504), b"<html>at capacity</html>", failure(429), JSON)
    assert osm.call("q", opener, waits.append) == JSON
    assert waits == [5.0, 10.0, 20.0]


def test_a_request_we_got_wrong_is_not_retried():
    with pytest.raises(osm.OverpassError):
        osm.call("q", Scripted(failure(400)), lambda _: None)


def test_it_gives_up_after_the_last_attempt():
    opener = Scripted(*[failure(504)] * osm.ATTEMPTS)
    with pytest.raises(osm.OverpassError):
        osm.call("q", opener, lambda _: None)
    assert opener.calls == osm.ATTEMPTS


def test_the_answer_lands_in_raw_once(store):
    raw = RawStore(store)
    opener = Scripted(JSON)
    assert osm.fetch(raw, KEY, "area-rodriguez", "q", "run-1", opener, lambda _: None) == JSON
    assert osm.fetch(raw, KEY, "area-rodriguez", "q", "run-2", opener, lambda _: None) == JSON
    assert opener.calls == 1
    assert len(raw.versions(Source.OSM, KEY, "area-rodriguez")) == 1
