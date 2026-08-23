import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from pitadvisor.ingest.fastf1_session import to_records
from pitadvisor.ingest.jolpica import materialize
from pitadvisor.ingest.raw_store import (
    META_SUFFIX,
    ObjectStore,
    write_bronze,
    write_quarantine,
)
from pitadvisor.ingest.weather import event_circuits
from pitadvisor.ingest.weather import parse as parse_weather
from pitadvisor.quality import contracts
from pitadvisor.types import EventKey, IngestOutcome, Layer, SessionKey, SessionKind, Source

RAW_FILE = re.compile(r"^(?P<name>.+)-(?P<stamp>\d{8}T\d{9})Z\.(?P<suffix>[A-Za-z0-9]+)$")
PAGED = re.compile(r"^(?P<resource>.+)-offset(?P<offset>\d+)$")
WEATHER_NAME = re.compile(r"^weather-(?P<kind>forecast|archive)$")


class RawObject(BaseModel, frozen=True):
    key: str
    source: Source
    season: int
    round: int
    session: SessionKind | None
    name: str
    stamp: str

    @property
    def event(self) -> EventKey:
        return EventKey(season=self.season, round=self.round)


def _value(part: str, prefix: str) -> str | None:
    head, sep, tail = part.partition("=")
    return tail if sep and head == prefix else None


def _member[T: (Source, SessionKind)](enum: type[T], value: str) -> T | None:
    return next((item for item in enum if item.value == value), None)


def parse_key(key: str) -> RawObject | None:
    if key.endswith(META_SUFFIX):
        return None
    parts = key.split("/")
    if len(parts) not in (5, 6) or parts[0] != Layer.RAW:
        return None
    source = _value(parts[1], "source")
    season = _value(parts[2], "season")
    round_ = _value(parts[3], "round")
    if source is None or season is None or round_ is None:
        return None
    if not season.isdigit() or not round_.isdigit():
        return None
    known = _member(Source, source)
    if known is None:
        return None
    session = None
    if len(parts) == 6:
        raw_session = _value(parts[4], "session")
        if raw_session is None:
            return None
        session = _member(SessionKind, raw_session)
        if session is None:
            return None
    matched = RAW_FILE.match(parts[-1])
    if matched is None:
        return None
    return RawObject(
        key=key,
        source=known,
        season=int(season),
        round=int(round_),
        session=session,
        name=matched.group("name"),
        stamp=matched.group("stamp"),
    )


def latest_objects(store: ObjectStore, prefix: str = f"{Layer.RAW}/") -> list[RawObject]:
    """A retried fetch lands a second copy under a later stamp, only the newest one replays."""
    newest: dict[tuple[Source, int, int, SessionKind | None, str], RawObject] = {}
    for item in store.list(prefix):
        found = parse_key(item.key)
        if found is None:
            continue
        marker = (found.source, found.season, found.round, found.session, found.name)
        current = newest.get(marker)
        if current is None or found.stamp > current.stamp:
            newest[marker] = found
    return sorted(newest.values(), key=lambda found: found.key)


def _payload(store: ObjectStore, found: RawObject) -> Any:
    return json.loads(store.get(found.key))


def _pages(objects: Iterable[RawObject]) -> list[RawObject]:
    def offset(found: RawObject) -> int:
        matched = PAGED.match(found.name)
        return int(matched.group("offset")) if matched else 0

    return sorted(objects, key=offset)


def rebuild_jolpica(
    store: ObjectStore, run_id: str, objects: list[RawObject], stamp: dict[str, Any]
) -> list[IngestOutcome]:
    grouped: dict[tuple[int, int, str], list[RawObject]] = {}
    for found in objects:
        matched = PAGED.match(found.name)
        if matched is None:
            continue
        resource = matched.group("resource")
        if resource not in contracts.TABLES:
            continue
        grouped.setdefault((found.season, found.round, resource), []).append(found)
    outcomes: list[IngestOutcome] = []
    for (season, round_, resource), pages in sorted(grouped.items()):
        ordered = _pages(pages)
        key = EventKey(season=season, round=round_)
        outcomes.append(
            materialize(
                run_id,
                store,
                key,
                resource,
                [_payload(store, found) for found in ordered],
                [found.key for found in ordered],
                False,
                stamp,
                requests=0,
            )
        )
    return outcomes


def rebuild_weather(
    store: ObjectStore, run_id: str, objects: list[RawObject], stamp: dict[str, Any]
) -> list[IngestOutcome]:
    wanted = [found for found in objects if WEATHER_NAME.match(found.name)]
    circuits: dict[tuple[int, int], str] = {}
    for season in sorted({found.season for found in wanted}):
        for circuit in event_circuits(store, season):
            circuits[(circuit.season, circuit.round)] = circuit.circuit_id
    outcomes: list[IngestOutcome] = []
    for found in wanted:
        circuit_id = circuits.get((found.season, found.round))
        if circuit_id is None:
            outcomes.append(
                IngestOutcome(
                    source=Source.OPEN_METEO,
                    table="weather",
                    season=found.season,
                    round=found.round,
                    skipped="no circuit in bronze races",
                )
            )
            continue
        # whether it was a forecast is a fact about the fetch, and recomputing it against
        # today's date would relabel every past snapshot as an archive read
        forecast = found.name == "weather-forecast"
        key = found.event
        records = parse_weather(_payload(store, found), circuit_id, forecast, stamp, key)
        kept, dropped = contracts.validate("weather", contracts.WeatherRow, records)
        write_quarantine(store, "weather", key, run_id, dropped)
        outcomes.append(
            IngestOutcome(
                source=Source.OPEN_METEO,
                table="weather",
                season=key.season,
                round=key.round,
                rows=len(kept),
                quarantined=len(dropped),
                raw_objects=[found.key],
                bronze_objects=[write_bronze(store, "weather", key, kept)] if kept else [],
            )
        )
    return outcomes


def rebuild_sessions(
    store: ObjectStore, run_id: str, objects: list[RawObject], stamp: dict[str, Any]
) -> list[IngestOutcome]:
    outcomes: list[IngestOutcome] = []
    for found in objects:
        if found.name != "session_laps" or found.session is None:
            continue
        key = SessionKey(season=found.season, round=found.round, session=found.session)
        records = to_records(_payload(store, found), key, stamp)
        kept, dropped = contracts.validate("session_laps", contracts.SessionLapRow, records)
        write_quarantine(store, "session_laps", key, run_id, dropped)
        outcomes.append(
            IngestOutcome(
                source=Source.FASTF1,
                table="session_laps",
                season=key.season,
                round=key.round,
                rows=len(kept),
                quarantined=len(dropped),
                raw_objects=[found.key],
                bronze_objects=[write_bronze(store, "session_laps", key, kept)] if kept else [],
            )
        )
    return outcomes


def rebuild_bronze(
    store: ObjectStore,
    run_id: str,
    season: int | None = None,
    source: Source | None = None,
) -> list[IngestOutcome]:
    stamp = {"run_id": run_id, "ingested_at": datetime.now(UTC)}
    found = [
        item
        for item in latest_objects(store)
        if (season is None or item.season == season) and (source is None or item.source == source)
    ]
    by_source: dict[Source, list[RawObject]] = {}
    for item in found:
        by_source.setdefault(item.source, []).append(item)
    # races carry the circuit ids weather needs, so jolpica is replayed before open-meteo
    return [
        *rebuild_jolpica(store, run_id, by_source.get(Source.JOLPICA, []), stamp),
        *rebuild_weather(store, run_id, by_source.get(Source.OPEN_METEO, []), stamp),
        *rebuild_sessions(store, run_id, by_source.get(Source.FASTF1, []), stamp),
    ]
