import json
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from pitadvisor.ingest.raw_store import ObjectStore, RawStore, write_bronze, write_quarantine
from pitadvisor.quality import contracts
from pitadvisor.types import (
    IngestOutcome,
    Layer,
    Provenance,
    SessionKey,
    SessionKind,
    Source,
    bronze_key,
)

CACHE_PREFIX = f"{Layer.CACHE}/fastf1/"

IDENTIFIERS: dict[SessionKind, str] = {
    SessionKind.FP1: "FP1",
    SessionKind.FP2: "FP2",
    SessionKind.FP3: "FP3",
    SessionKind.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionKind.SPRINT_SHOOTOUT: "Sprint Shootout",
    SessionKind.SPRINT: "Sprint",
    SessionKind.QUALIFYING: "Qualifying",
    SessionKind.RACE: "Race",
}

CONVENTIONAL = (
    SessionKind.FP1,
    SessionKind.FP2,
    SessionKind.FP3,
    SessionKind.QUALIFYING,
    SessionKind.RACE,
)

# a sprint weekend has one practice, and the saturday session changed name twice in three years
FORMATS: dict[str, tuple[SessionKind, ...]] = {
    "conventional": CONVENTIONAL,
    "testing": (SessionKind.FP1, SessionKind.FP2, SessionKind.FP3),
    "sprint": (
        SessionKind.FP1,
        SessionKind.QUALIFYING,
        SessionKind.FP2,
        SessionKind.SPRINT,
        SessionKind.RACE,
    ),
    "sprint_shootout": (
        SessionKind.FP1,
        SessionKind.QUALIFYING,
        SessionKind.SPRINT_SHOOTOUT,
        SessionKind.SPRINT,
        SessionKind.RACE,
    ),
    "sprint_qualifying": (
        SessionKind.FP1,
        SessionKind.SPRINT_QUALIFYING,
        SessionKind.SPRINT,
        SessionKind.QUALIFYING,
        SessionKind.RACE,
    ),
}


class UnknownFormatError(ValueError):
    def __init__(self, event_format: str) -> None:
        super().__init__(f"unknown event format {event_format!r}")


def sessions_for(event_format: str) -> tuple[SessionKind, ...]:
    try:
        return FORMATS[event_format]
    except KeyError as exc:
        raise UnknownFormatError(event_format) from exc


def millis(value: Any) -> int | None:
    if value is None:
        return None
    seconds = getattr(value, "total_seconds", None)
    if seconds is None:
        return None
    total = cast(float, seconds())
    if total != total or total <= 0:  # NaT comes back as nan
        return None
    return round(total * 1000)


def load_laps(season: int, round_: int, session: SessionKind, cache_dir: Path) -> Any:
    # optional extra: only the session ingest needs it, and it drags in pandas
    import fastf1  # pyright: ignore[reportMissingImports]

    api: Any = fastf1
    cache_dir.mkdir(parents=True, exist_ok=True)
    api.Cache.enable_cache(str(cache_dir))
    loaded: Any = api.get_session(season, round_, IDENTIFIERS[session])
    loaded.load(laps=True, telemetry=False, weather=False, messages=False)
    return loaded.laps


def _jsonable(value: Any) -> Any:
    if value is None or value != value:  # NaT and NaN both fail this
        return None
    if hasattr(value, "total_seconds"):
        return millis(value)
    if hasattr(value, "isoformat"):
        return cast(str, value.isoformat())
    if isinstance(value, bool | int | float | str):
        return value
    scalar = getattr(value, "item", None)  # numpy types come out of pandas everywhere
    return _jsonable(scalar()) if scalar is not None else str(value)


def serialize(laps: Any) -> list[dict[str, Any]]:
    """The raw copy: every column fastf1 gave us, only made JSON-safe."""
    rows = cast(list[dict[str, Any]], laps.to_dict("records"))
    return [{str(name): _jsonable(value) for name, value in row.items()} for row in rows]


def session_url(key: SessionKey) -> str:
    return f"fastf1://{key.season}/{key.round:02d}/{key.session}"


def to_records(
    payload: list[dict[str, Any]], key: SessionKey, stamp: dict[str, Any]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: dict[tuple[str, int], int] = {}
    for lap in payload:
        driver = str(lap.get("Driver") or "")
        stint_number = _int_or_none(lap.get("Stint"))
        lap_in_stint = None
        if stint_number is not None:
            marker = (driver, stint_number)
            seen[marker] = seen.get(marker, 0) + 1
            lap_in_stint = seen[marker]
        records.append(
            {
                **stamp,
                "season": key.season,
                "round": key.round,
                "session": key.session,
                "driver_code": driver,
                "driver_number": lap.get("DriverNumber"),
                "lap": lap.get("LapNumber"),
                "lap_time_millis": lap.get("LapTime"),
                "sector1_millis": lap.get("Sector1Time"),
                "sector2_millis": lap.get("Sector2Time"),
                "sector3_millis": lap.get("Sector3Time"),
                "stint": stint_number,
                "lap_in_stint": lap_in_stint,
                "compound": lap.get("Compound"),
                "tyre_life": _int_or_none(lap.get("TyreLife")),
                "is_personal_best": bool(lap.get("IsPersonalBest") or False),
                "is_deleted": bool(lap.get("Deleted") or False),
                "is_accurate": bool(lap.get("IsAccurate") or False),
                "track_status": str(lap.get("TrackStatus") or "") or None,
                "pit_in": lap.get("PitInTime") is not None,
                "pit_out": lap.get("PitOutTime") is not None,
                "position": _int_or_none(lap.get("Position")),
            }
        )
    return records


def _int_or_none(value: Any) -> int | None:
    if value is None or value != value:
        return None
    return int(value)


def pull_cache(store: ObjectStore, cache_dir: Path) -> int:
    """The fargate task starts with an empty disk and a cold fastf1 cache costs hours."""
    pulled = 0
    for item in store.list(CACHE_PREFIX):
        target = cache_dir / item.key[len(CACHE_PREFIX) :]
        if target.is_file() and target.stat().st_size == item.size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.get(item.key))
        pulled += 1
    return pulled


def push_cache(store: ObjectStore, cache_dir: Path) -> int:
    if not cache_dir.is_dir():
        return 0
    known = {item.key: item.size for item in store.list(CACHE_PREFIX)}
    pushed = 0
    for path in sorted(cache_dir.rglob("*")):
        if not path.is_file():
            continue
        key = CACHE_PREFIX + path.relative_to(cache_dir).as_posix()
        if known.get(key) == path.stat().st_size:
            continue
        store.put(key, path.read_bytes())
        pushed += 1
    return pushed


def ingest_session(
    store: ObjectStore,
    key: SessionKey,
    cache_dir: Path,
    run_id: str = "local",
    loader: Callable[[int, int, SessionKind, Path], Any] = load_laps,
    sync_cache: bool = False,
) -> IngestOutcome:
    fetched_at = datetime.now(UTC)
    stamp = {"run_id": run_id, "ingested_at": fetched_at}
    if sync_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        pull_cache(store, cache_dir)
    payload = serialize(loader(key.season, key.round, key.session, cache_dir))
    if sync_cache:
        push_cache(store, cache_dir)
    landed = RawStore(store).land(
        key,
        "session_laps",
        json.dumps(payload).encode(),
        Provenance(
            run_id=run_id,
            source=Source.FASTF1,
            url=session_url(key),
            fetched_at=fetched_at,
            status=200,
        ),
    )
    kept, dropped = contracts.validate(
        "session_laps", contracts.SessionLapRow, to_records(payload, key, stamp)
    )
    write_quarantine(store, "session_laps", key, run_id, dropped)
    return IngestOutcome(
        source=Source.FASTF1,
        table="session_laps",
        season=key.season,
        round=key.round,
        rows=len(kept),
        quarantined=len(dropped),
        raw_objects=[landed],
        bronze_objects=[write_bronze(store, "session_laps", key, kept)] if kept else [],
    )


def completed_events(season: int, cache_dir: Path, today: date | None = None) -> dict[int, str]:
    import fastf1  # pyright: ignore[reportMissingImports]

    api: Any = fastf1
    cache_dir.mkdir(parents=True, exist_ok=True)
    api.Cache.enable_cache(str(cache_dir))
    schedule: Any = api.get_event_schedule(season, include_testing=False)
    return held_events(schedule.to_dict("records"), today or datetime.now(UTC).date())


def held_events(events: list[dict[str, Any]], cutoff: date) -> dict[int, str]:
    """A season in progress still lists the rounds it has not run yet."""
    held: dict[int, str] = {}
    for event in events:
        when = event["EventDate"]
        if hasattr(when, "date"):
            when = when.date()
        if when > cutoff:
            continue
        held[int(event["RoundNumber"])] = str(event["EventFormat"])
    return held


def backfill(
    store: ObjectStore,
    season: int,
    cache_dir: Path,
    run_id: str = "local",
    kinds: Iterable[SessionKind] = (SessionKind.RACE,),
    loader: Callable[[int, int, SessionKind, Path], Any] = load_laps,
    schedule: Callable[[int, Path], dict[int, str]] = completed_events,
    sync_cache: bool = False,
    skip_present: bool = True,
) -> list[IngestOutcome]:
    wanted = set(kinds)
    outcomes: list[IngestOutcome] = []
    for round_, event_format in sorted(schedule(season, cache_dir).items()):
        for kind in sessions_for(event_format):
            if kind not in wanted:
                continue
            key = SessionKey(season=season, round=round_, session=kind)
            if skip_present and store.exists(bronze_key("session_laps", key)):
                outcomes.append(
                    IngestOutcome(
                        source=Source.FASTF1,
                        table="session_laps",
                        season=season,
                        round=round_,
                        skipped="already in bronze",
                    )
                )
                continue
            outcomes.append(ingest_session(store, key, cache_dir, run_id, loader, sync_cache))
    return outcomes
