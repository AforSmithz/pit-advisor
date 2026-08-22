import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, cast

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import (
    ObjectStore,
    RawStore,
    write_bronze_by_event,
    write_quarantine,
)
from pitadvisor.quality import contracts
from pitadvisor.quality.contracts import parse_duration_millis
from pitadvisor.types import EventKey, IngestOutcome, Provenance, Source, bronze_key

BASE_URL = "https://api.jolpi.ca/ergast/f1"
PAGE_LIMIT = 100
MAX_PAGES = 40
RESOURCES = ("races", "results", "qualifying", "laps", "pitstops")


class RawMissingError(RuntimeError):
    def __init__(self, key: str) -> None:
        super().__init__(f"upstream answered 304 but {key} is not in raw/")


def endpoint(season: int, round_: int | None, resource: str, offset: int = 0) -> str:
    path = f"{BASE_URL}/{season}"
    if round_ is not None:
        path = f"{path}/{round_}"
    if resource != "races":
        path = f"{path}/{resource}"
    return f"{path}.json?limit={PAGE_LIMIT}&offset={offset}"


def plan(season: int, rounds: Iterable[int], resources: Iterable[str] = RESOURCES) -> list[str]:
    urls: list[str] = []
    for resource in resources:
        if resource == "races":
            urls.append(endpoint(season, None, resource))
            continue
        urls.extend(endpoint(season, round_, resource) for round_ in rounds)
    return urls


def races_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    table: Any = payload.get("MRData", {}).get("RaceTable", {})
    return list(table.get("Races", []))


def _nested(record: dict[str, Any], key: str) -> dict[str, Any]:
    value: Any = record.get(key)
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _total(payload: dict[str, Any]) -> int:
    return int(payload.get("MRData", {}).get("total", 0))


def _start_utc(race: dict[str, Any]) -> datetime | None:
    stamp = race.get("time")
    if not stamp:
        return None
    return datetime.fromisoformat(f"{race['date']}T{stamp.replace('Z', '+00:00')}")


def parse_races(payloads: list[dict[str, Any]], stamp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for race in races_of(payload):
            circuit = _nested(race, "Circuit")
            location = _nested(circuit, "Location")
            rows.append(
                {
                    **stamp,
                    "season": race.get("season"),
                    "round": race.get("round"),
                    "race_name": race.get("raceName"),
                    "circuit_id": circuit.get("circuitId"),
                    "circuit_name": circuit.get("circuitName"),
                    "locality": location.get("locality"),
                    "country": location.get("country"),
                    "latitude": location.get("lat"),
                    "longitude": location.get("long"),
                    "race_date": race.get("date"),
                    "start_utc": _start_utc(race),
                }
            )
    return rows


def parse_results(payloads: list[dict[str, Any]], stamp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for race in races_of(payload):
            for result in race.get("Results", []):
                fastest = _nested(result, "FastestLap")
                rows.append(
                    {
                        **stamp,
                        "season": race.get("season"),
                        "round": race.get("round"),
                        "driver_id": _nested(result, "Driver").get("driverId"),
                        "constructor_id": _nested(result, "Constructor").get("constructorId"),
                        "car_number": result.get("number"),
                        "grid": result.get("grid"),
                        "position": result.get("position"),
                        "position_text": result.get("positionText"),
                        "points": result.get("points"),
                        "laps_completed": result.get("laps"),
                        "status": result.get("status"),
                        "time_millis": _nested(result, "Time").get("millis"),
                        "fastest_lap_rank": fastest.get("rank"),
                        "fastest_lap_millis": parse_duration_millis(
                            _nested(fastest, "Time").get("time")
                        ),
                    }
                )
    return rows


def parse_qualifying(payloads: list[dict[str, Any]], stamp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for race in races_of(payload):
            for entry in race.get("QualifyingResults", []):
                rows.append(
                    {
                        **stamp,
                        "season": race.get("season"),
                        "round": race.get("round"),
                        "driver_id": _nested(entry, "Driver").get("driverId"),
                        "constructor_id": _nested(entry, "Constructor").get("constructorId"),
                        "position": entry.get("position"),
                        "q1_millis": parse_duration_millis(entry.get("Q1")),
                        "q2_millis": parse_duration_millis(entry.get("Q2")),
                        "q3_millis": parse_duration_millis(entry.get("Q3")),
                    }
                )
    return rows


def parse_laps(payloads: list[dict[str, Any]], stamp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for race in races_of(payload):
            for lap in race.get("Laps", []):
                for timing in lap.get("Timings", []):
                    rows.append(
                        {
                            **stamp,
                            "season": race.get("season"),
                            "round": race.get("round"),
                            "driver_id": timing.get("driverId"),
                            "lap": lap.get("number"),
                            "position": timing.get("position"),
                            "time_millis": parse_duration_millis(timing.get("time")),
                        }
                    )
    return rows


def parse_pitstops(payloads: list[dict[str, Any]], stamp: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        for race in races_of(payload):
            for stop in race.get("PitStops", []):
                rows.append(
                    {
                        **stamp,
                        "season": race.get("season"),
                        "round": race.get("round"),
                        "driver_id": stop.get("driverId"),
                        "stop": stop.get("stop"),
                        "lap": stop.get("lap"),
                        "time_of_day": stop.get("time"),
                        "duration_millis": parse_duration_millis(stop.get("duration")),
                    }
                )
    return rows


PARSERS: dict[str, Callable[[list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]] = {
    "races": parse_races,
    "results": parse_results,
    "qualifying": parse_qualifying,
    "laps": parse_laps,
    "pitstops": parse_pitstops,
}


class JolpicaClient:
    def __init__(
        self,
        raw: RawStore,
        ledger: Ledger,
        limiter: RateLimiter | None = None,
        run_id: str = "local",
        fetch: Callable[..., http.Response] | None = None,
    ) -> None:
        self.raw = raw
        self.ledger = ledger
        self.limiter = limiter
        self.run_id = run_id
        self.fetch = fetch or http.fetch

    def _page(
        self, key: EventKey, resource: str, offset: int
    ) -> tuple[dict[str, Any], str | None, bool]:
        # the schedule is a season request, so it lands under round 1 whoever asked for it
        raw_target = EventKey(season=key.season, round=1) if resource == "races" else key
        url = endpoint(key.season, None if resource == "races" else key.round, resource, offset)
        name = f"{resource}-offset{offset:04d}"
        response = self.fetch(url, self.ledger, self.limiter)
        if response.not_modified:
            cached = self.raw.latest(Source.JOLPICA, raw_target, name)
            if cached is not None:
                return json.loads(cached[0]), None, True
            response = self.fetch(url, http.Unconditional(self.ledger), self.limiter)
            if response.not_modified:
                raise RawMissingError(name)
        uri = self.raw.land(
            raw_target,
            name,
            response.body,
            Provenance(
                run_id=self.run_id,
                source=Source.JOLPICA,
                url=url,
                fetched_at=response.fetched_at,
                status=response.status,
                etag=response.etag,
            ),
        )
        return json.loads(response.body), uri, False

    def pages(self, key: EventKey, resource: str) -> tuple[list[dict[str, Any]], list[str], bool]:
        payloads: list[dict[str, Any]] = []
        landed: list[str] = []
        cached_only = True
        offset = 0
        for _ in range(MAX_PAGES):
            payload, uri, not_modified = self._page(key, resource, offset)
            payloads.append(payload)
            if uri is not None:
                landed.append(uri)
            cached_only = cached_only and not_modified
            offset += PAGE_LIMIT
            if offset >= _total(payload):
                break
        return payloads, landed, cached_only


def materialize(
    client: JolpicaClient,
    store: ObjectStore,
    key: EventKey,
    resource: str,
    payloads: list[dict[str, Any]],
    landed: list[str],
    cached_only: bool,
    stamp: dict[str, Any],
) -> IngestOutcome:
    records = PARSERS[resource](payloads, stamp)
    model = contracts.TABLES[resource]
    kept, dropped = contracts.validate(resource, model, records)
    write_quarantine(store, resource, key, client.run_id, dropped)
    return IngestOutcome(
        source=Source.JOLPICA,
        table=resource,
        season=key.season,
        round=key.round,
        rows=len(kept),
        quarantined=len(dropped),
        raw_objects=landed,
        bronze_objects=write_bronze_by_event(store, resource, kept),
        requests=len(payloads),
        not_modified=cached_only,
    )


def ingest_event(
    client: JolpicaClient,
    store: ObjectStore,
    key: EventKey,
    resources: Iterable[str] = RESOURCES,
    skip_present: bool = False,
) -> list[IngestOutcome]:
    outcomes: list[IngestOutcome] = []
    stamp = {"run_id": client.run_id, "ingested_at": datetime.now(UTC)}
    for resource in resources:
        if skip_present and store.exists(bronze_key(resource, key)):
            outcomes.append(
                IngestOutcome(
                    source=Source.JOLPICA,
                    table=resource,
                    season=key.season,
                    round=key.round,
                    skipped="already in bronze",
                )
            )
            continue
        payloads, landed, cached_only = client.pages(key, resource)
        outcomes.append(
            materialize(client, store, key, resource, payloads, landed, cached_only, stamp)
        )
    return outcomes


def rounds_in(payloads: list[dict[str, Any]]) -> list[int]:
    return sorted({int(race["round"]) for payload in payloads for race in races_of(payload)})


def backfill(
    client: JolpicaClient,
    store: ObjectStore,
    season: int,
    resources: Iterable[str] = RESOURCES,
    skip_present: bool = True,
) -> list[IngestOutcome]:
    wanted = [resource for resource in resources if resource != "races"]
    stamp = {"run_id": client.run_id, "ingested_at": datetime.now(UTC)}
    schedule_key = EventKey(season=season, round=1)
    # the schedule is one request and it is what says which rounds exist, so it is never skipped
    payloads, landed, cached_only = client.pages(schedule_key, "races")
    outcomes = [
        materialize(client, store, schedule_key, "races", payloads, landed, cached_only, stamp)
    ]
    for round_ in rounds_in(payloads):
        key = EventKey(season=season, round=round_)
        outcomes.extend(ingest_event(client, store, key, wanted, skip_present))
    return outcomes
