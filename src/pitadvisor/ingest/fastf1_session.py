from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pitadvisor.ingest.raw_store import ObjectStore, write_bronze, write_quarantine
from pitadvisor.quality import contracts
from pitadvisor.types import IngestOutcome, SessionKey, SessionKind, Source

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


def to_records(laps: Any, key: SessionKey, stamp: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: dict[tuple[str, int], int] = {}
    for lap in cast(list[dict[str, Any]], laps.to_dict("records")):
        stint = lap.get("Stint")
        driver = str(lap.get("Driver") or "")
        stint_number = _int_or_none(stint)
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
                "lap_time_millis": millis(lap.get("LapTime")),
                "sector1_millis": millis(lap.get("Sector1Time")),
                "sector2_millis": millis(lap.get("Sector2Time")),
                "sector3_millis": millis(lap.get("Sector3Time")),
                "stint": stint_number,
                "lap_in_stint": lap_in_stint,
                "compound": lap.get("Compound"),
                "tyre_life": _int_or_none(lap.get("TyreLife")),
                "is_personal_best": bool(lap.get("IsPersonalBest") or False),
                "is_deleted": bool(lap.get("Deleted") or False),
                "is_accurate": bool(lap.get("IsAccurate") or False),
                "track_status": str(lap.get("TrackStatus") or "") or None,
                "pit_in": _present(lap.get("PitInTime")),
                "pit_out": _present(lap.get("PitOutTime")),
                "position": _int_or_none(lap.get("Position")),
            }
        )
    return records


def _present(value: Any) -> bool:
    return value is not None and value == value


def _int_or_none(value: Any) -> int | None:
    if value is None or value != value:
        return None
    return int(value)


def ingest_session(
    store: ObjectStore,
    key: SessionKey,
    cache_dir: Path,
    run_id: str = "local",
    loader: Callable[[int, int, SessionKind, Path], Any] = load_laps,
) -> IngestOutcome:
    stamp = {"run_id": run_id, "ingested_at": datetime.now(UTC)}
    laps = loader(key.season, key.round, key.session, cache_dir)
    records = to_records(laps, key, stamp)
    kept, dropped = contracts.validate("session_laps", contracts.SessionLapRow, records)
    write_quarantine(store, "session_laps", key, run_id, dropped)
    return IngestOutcome(
        source=Source.FASTF1,
        table="session_laps",
        season=key.season,
        round=key.round,
        rows=len(kept),
        quarantined=len(dropped),
        bronze_objects=[write_bronze(store, "session_laps", key, kept)] if kept else [],
    )
