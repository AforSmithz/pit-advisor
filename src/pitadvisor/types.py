from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Layer(StrEnum):
    RAW = "raw"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    VIEWS = "views"
    QUARANTINE = "quarantine"
    DOCS = "docs"
    CACHE = "cache"


class Source(StrEnum):
    JOLPICA = "jolpica"
    FASTF1 = "fastf1"
    OPEN_METEO = "open_meteo"
    FIA_DOCS = "fia_docs"
    CURATED = "curated"


class SessionKind(StrEnum):
    FP1 = "fp1"
    FP2 = "fp2"
    FP3 = "fp3"
    # 2023 ran it as the sprint shootout, 2024 went back to sprint quali. both stay.
    SPRINT_QUALIFYING = "sprint_qualifying"
    SPRINT_SHOOTOUT = "sprint_shootout"
    SPRINT = "sprint"
    QUALIFYING = "qualifying"
    RACE = "race"


class EventKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    season: int = Field(ge=1950, le=2100)
    round: int = Field(ge=1, le=30)


class SessionKey(EventKey):
    session: SessionKind


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    source: Source
    url: str
    fetched_at: datetime
    status: int
    etag: str | None = None

    @field_validator("fetched_at")
    @classmethod
    def _utc_only(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("fetched_at must be timezone-aware UTC")
        return value


def raw_key(source: Source, key: SessionKey | EventKey, filename: str) -> str:
    parts: list[str] = [
        Layer.RAW,
        f"source={source}",
        f"season={key.season}",
        f"round={key.round:02d}",
    ]
    if isinstance(key, SessionKey):
        parts.append(f"session={key.session}")
    parts.append(filename)
    return "/".join(parts)


def raw_filename(name: str, fetched_at: datetime, suffix: str = "json") -> str:
    # milliseconds, because a retry inside the same second is a normal thing to happen
    stamp = fetched_at.strftime("%Y%m%dT%H%M%S%f")[:-3]
    return f"{name}-{stamp}Z.{suffix}"


def bronze_key(table: str, key: SessionKey | EventKey, filename: str | None = None) -> str:
    parts: list[str] = [
        Layer.BRONZE,
        f"table={table}",
        f"season={key.season}",
        f"round={key.round:02d}",
    ]
    if isinstance(key, SessionKey):
        parts.append(f"session={key.session}")
    parts.append(filename or f"{table}.parquet")
    return "/".join(parts)


def quarantine_key(table: str, key: SessionKey | EventKey, run_id: str) -> str:
    parts: list[str] = [
        Layer.QUARANTINE,
        f"table={table}",
        f"season={key.season}",
        f"round={key.round:02d}",
    ]
    if isinstance(key, SessionKey):
        parts.append(f"session={key.session}")
    parts.append(f"run={run_id}.jsonl")
    return "/".join(parts)


class IngestOutcome(BaseModel, frozen=True):
    source: Source
    table: str
    season: int
    round: int
    rows: int = 0
    quarantined: int = 0
    raw_objects: list[str] = Field(default_factory=list)
    bronze_objects: list[str] = Field(default_factory=list)
    requests: int = 0
    not_modified: bool = False
    skipped: str | None = None
