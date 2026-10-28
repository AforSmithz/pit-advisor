import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from pitadvisor.ingest.http import USER_AGENT
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Provenance, Source

ENDPOINT: Final = "https://overpass-api.de/api/interpreter"
RETRY: Final = frozenset({429, 502, 503, 504})
ATTEMPTS: Final = 6
TIMEOUT: Final = 180.0
MAX_WAIT: Final = 120.0
AREA_FEATURES: Final = (
    'way["building"]',
    'relation["building"]',
    'way["landuse"]',
    'relation["landuse"]',
    'way["leisure"]',
    'relation["leisure"]',
    'way["natural"]',
    'relation["natural"]',
    'way["highway"]',
    'way["amenity"="parking"]',
    'way["railway"]',
    'node["natural"="tree"]',
)

Opener = Callable[[urllib.request.Request, float], Any]
Sleep = Callable[[float], None]


class OverpassError(RuntimeError):
    pass


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def raceway_query(lat: float, lon: float, radius_m: float) -> str:
    return (
        f'[out:json][timeout:60];way["highway"="raceway"]'
        f"(around:{radius_m:.0f},{lat:.6f},{lon:.6f});out geom;"
    )


def area_query(south: float, west: float, north: float, east: float) -> str:
    box = f"({south:.5f},{west:.5f},{north:.5f},{east:.5f})"
    return (
        f"[out:json][timeout:120];({''.join(f'{item}{box};' for item in AREA_FEATURES)});out geom;"
    )


def query_url(query: str) -> str:
    return f"{ENDPOINT}?data={urllib.parse.quote(query)}"


def call(query: str, opener: Opener = _open, sleep: Sleep = time.sleep) -> bytes:
    request = urllib.request.Request(query_url(query), headers={"User-Agent": USER_AGENT})
    for attempt in range(ATTEMPTS):
        wait = min(MAX_WAIT, 5.0 * 2**attempt)
        try:
            with opener(request, TIMEOUT) as raw:
                body = bytes(raw.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY:
                raise OverpassError(f"overpass answered {exc.code}") from exc
            sleep(wait)
            continue
        except (urllib.error.URLError, TimeoutError):
            sleep(wait)
            continue
        # a busy server sometimes answers 200 with an html page instead of the json asked for
        if body.lstrip()[:1] == b"{":
            return body
        sleep(wait)
    raise OverpassError(f"overpass still at capacity after {ATTEMPTS} attempts")


def fetch(
    raw: RawStore,
    key: EventKey,
    name: str,
    query: str,
    run_id: str,
    opener: Opener = _open,
    sleep: Sleep = time.sleep,
) -> bytes:
    held = raw.latest(Source.OSM, key, name)
    if held is not None:
        return held[0]
    body = call(query, opener, sleep)
    raw.land(
        key,
        name,
        body,
        Provenance(
            run_id=run_id,
            source=Source.OSM,
            url=query_url(query),
            fetched_at=datetime.now(UTC),
            status=200,
        ),
    )
    return body
