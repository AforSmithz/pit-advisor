import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel

from pitadvisor.ingest.http import USER_AGENT
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Provenance, Source

# the circuit maps fastf1 reads its corner numbers from
ENDPOINT: Final = "https://api.multiviewer.app/api/v1/circuits"
ATTEMPTS: Final = 3
TIMEOUT: Final = 30.0

Opener = Callable[[urllib.request.Request, float], Any]


class MultiViewerError(RuntimeError):
    pass


class Corner(BaseModel, frozen=True):
    number: int
    # metres, in the same frame as openf1's car positions
    x: float
    y: float


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def circuit_url(circuit_key: int, season: int) -> str:
    return f"{ENDPOINT}/{circuit_key}/{season}"


def call(target: str, opener: Opener = _open, sleep: Callable[[float], None] = time.sleep) -> bytes:
    request = urllib.request.Request(target, headers={"User-Agent": USER_AGENT})
    for attempt in range(ATTEMPTS):
        try:
            with opener(request, TIMEOUT) as raw:
                return bytes(raw.read())
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise MultiViewerError(f"multiviewer answered {exc.code} for {target}") from exc
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead):
            pass
        sleep(5.0 * 2**attempt)
    raise MultiViewerError(f"multiviewer did not answer {target} after {ATTEMPTS} attempts")


def fetch(
    raw: RawStore,
    key: EventKey,
    circuit_key: int,
    run_id: str,
    opener: Opener = _open,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    name = f"circuit-{circuit_key}"
    held = raw.latest(Source.MULTIVIEWER, key, name)
    if held is not None:
        return held[0]
    target = circuit_url(circuit_key, key.season)
    body = call(target, opener, sleep)
    raw.land(
        key,
        name,
        body,
        Provenance(
            run_id=run_id,
            source=Source.MULTIVIEWER,
            url=target,
            fetched_at=datetime.now(UTC),
            status=200,
        ),
    )
    return body


def corners(body: bytes) -> list[Corner]:
    # positions are decimetres, like openf1's
    return [
        Corner(
            number=int(item["number"]),
            x=float(item["trackPosition"]["x"]) / 10.0,
            y=float(item["trackPosition"]["y"]) / 10.0,
        )
        for item in json.loads(body).get("corners", [])
    ]
