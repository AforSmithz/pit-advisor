# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, cast

import polars as pl

from pitadvisor.ingest.http import USER_AGENT
from pitadvisor.ingest.raw_store import RawStore, write_bronze_by_event, write_quarantine
from pitadvisor.quality import contracts
from pitadvisor.types import EventKey, Provenance, Source

ENDPOINT: Final = "https://api.typesafe.ai/v1/systemone"
# pinned: a response from another version answers a different question and is never mixed in
MODEL: Final = "jev-1.13.0"
QUESTION_VERSION: Final = "u3-1"
RETRY: Final = frozenset({429, 529})
ATTEMPTS: Final = 5
TIMEOUT: Final = 30.0
LEVELS: Final = (
    "A reliability, cooling, or circuit-specific downforce change that adds no general car pace.",
    "A refinement of an existing part, such as a revised flap, a trim, or a detail change to "
    "an edge or a fence.",
    "New geometry on one aerodynamic surface or component, such as a new front wing, a new "
    "beam wing, or a new engine cover.",
    "A new floor, sidepod, or bodywork concept, or several linked surfaces redesigned together.",
)

Opener = Callable[[urllib.request.Request, float], Any]
Sleep = Callable[[float], None]


class JevError(RuntimeError):
    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"typesafe answered {status}{f': {detail}' if detail else ''}")
        self.status = status


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def request_body(
    race_name: str, circuit_id: str, team: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """One request per team and document. rows are that team's performance updates."""
    questions: dict[str, Any] = {}
    for index, row in enumerate(rows):
        ordinal = int(row["ordinal"])
        where = f"`updates[{index}]`"
        questions[f"effect_{ordinal}"] = {
            "type": "score",
            "instructions": (
                f"Judging only from how the team describes it, how much general car pace is "
                f"the update in {where} built to add, at any circuit?"
            ),
            "criteria": list(LEVELS),
        }
        questions[f"package_{ordinal}"] = {
            "type": "noul",
            "instructions": (
                f"Does the team present the update in {where} as part of a major upgrade "
                f"package brought to this event?"
            ),
            "criteria": {
                "true": "It is one piece of a larger, coordinated package of new parts.",
                "false": "It stands alone, or is a routine change for this circuit.",
            },
        }
    return {
        "model": MODEL,
        "state": {
            "event": race_name,
            "circuit": circuit_id,
            "team": team,
            "updates": [
                {"category": row["category"], "reason": row["reason"], "text": row["text"]}
                for row in rows
            ],
        },
        "questions": questions,
    }


def name_for(body: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20]
    return f"jev-{QUESTION_VERSION}-{digest}"


def call(
    body: dict[str, Any],
    api_key: str,
    opener: Opener = _open,
    sleep: Sleep = time.sleep,
) -> bytes:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    for attempt in range(ATTEMPTS):
        try:
            with opener(request, TIMEOUT) as raw:
                payload = cast(bytes, raw.read())
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY and attempt < ATTEMPTS - 1:
                sleep(2.0**attempt)
                continue
            # a 401 or 422 is our bug, retrying it only spends the credit
            raise JevError(int(exc.code), exc.read().decode(errors="replace")[:300]) from exc
        answered = json.loads(payload)
        if answered.get("model") != MODEL:
            raise JevError(200, f"answered by {answered.get('model')}, pinned {MODEL}")
        return payload
    raise JevError(0, "retries exhausted")


def readings(payload: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answers = payload.get("answers", {})
    found: list[dict[str, Any]] = []
    for row in rows:
        ordinal = int(row["ordinal"])
        effect = answers.get(f"effect_{ordinal}")
        package = answers.get(f"package_{ordinal}")
        if effect is None or package is None:
            continue
        found.append(
            {
                "ordinal": ordinal,
                "magnitude": float(effect["score"]),
                "package": float(package["noul"]),
                "model": str(payload.get("model")),
            }
        )
    return found


def score(
    raw: RawStore,
    updates: pl.DataFrame,
    races: pl.DataFrame,
    stamp: dict[str, Any],
    ask: Callable[[dict[str, Any]], bytes] | None,
    season: int | None = None,
) -> tuple[int, int, int]:
    """Lands a response per team and document, and writes the magnitudes bronze reads.
    Without `ask` only responses already landed are read, which is the replay path."""
    wanted = updates.filter((pl.col("ordinal") > 0) & (pl.col("category") == "performance"))
    if season is not None:
        wanted = wanted.filter(pl.col("season") == season)
    names = races.select("season", "round", "race_name", "circuit_id").unique(
        subset=["season", "round"], keep="last"
    )
    asked = 0
    records: list[dict[str, Any]] = []
    groups = wanted.sort("ordinal").group_by(
        "season", "round", "document_name", "constructor_id", maintain_order=True
    )
    for (season, round_, document, constructor), rows in groups:
        key = EventKey(season=int(cast(int, season)), round=int(cast(int, round_)))
        event = names.filter((pl.col("season") == key.season) & (pl.col("round") == key.round))
        if not event.height:
            continue
        listed = rows.to_dicts()
        body = request_body(
            str(event["race_name"][0]), str(event["circuit_id"][0]), str(listed[0]["team"]), listed
        )
        name = name_for(body)
        held = raw.latest(Source.TYPESAFE, key, name)
        if held is not None:
            payload = held[0]
        elif ask is not None:
            payload = ask(body)
            asked += 1
            raw.land(
                key,
                name,
                payload,
                Provenance(
                    run_id=str(stamp["run_id"]),
                    source=Source.TYPESAFE,
                    url=ENDPOINT,
                    fetched_at=datetime.now(UTC),
                    status=200,
                    etag=None,
                ),
            )
        else:
            continue
        raw_key = raw.versions(Source.TYPESAFE, key, name)[-1]
        for reading in readings(json.loads(payload), listed):
            records.append(
                {
                    **stamp,
                    "season": key.season,
                    "round": key.round,
                    "document_name": str(document),
                    "constructor_id": str(constructor),
                    "question_version": QUESTION_VERSION,
                    "raw_key": raw_key,
                    **reading,
                }
            )
    kept, dropped = contracts.validate(
        "car_update_magnitudes", contracts.CarUpdateMagnitudeRow, records
    )
    for event_key in {(int(row["season"]), int(row["round"])) for row in records}:
        write_quarantine(
            raw.store,
            "car_update_magnitudes",
            EventKey(season=event_key[0], round=event_key[1]),
            str(stamp["run_id"]),
            [
                item
                for item in dropped
                if (item.payload["season"], item.payload["round"]) == event_key
            ],
        )
    if kept:
        write_bronze_by_event(raw.store, "car_update_magnitudes", kept)
    return asked, len(kept), len(dropped)
