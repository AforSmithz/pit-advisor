import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pitadvisor.types import SessionKind

DURATION = re.compile(r"^(?:(?P<h>\d+):)?(?:(?P<m>\d+):)?(?P<s>\d+)(?:\.(?P<frac>\d{1,6}))?$")


class Reason(StrEnum):
    CONTRACT = "contract_violation"
    UNPARSEABLE_DURATION = "unparseable_duration"
    UNKNOWN_SESSION = "unknown_session"
    ORPHAN_DRIVER = "orphan_driver"
    DUPLICATE_KEY = "duplicate_key"
    MISSING_LAP_TIME = "missing_lap_time"


class Quarantined(BaseModel, frozen=True):
    table: str
    reason: Reason
    detail: str
    payload: dict[str, Any]


class BronzeRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    ingested_at: datetime
    season: int = Field(ge=1950, le=2100)
    round: int = Field(ge=1, le=30)


class RaceRow(BronzeRow):
    race_name: str
    circuit_id: str
    circuit_name: str
    locality: str | None = None
    country: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    race_date: date
    start_utc: datetime | None = None


class ResultRow(BronzeRow):
    driver_id: str
    # the only bridge to fastf1, which knows a driver by his three letters and nothing else
    driver_code: str | None = None
    constructor_id: str
    car_number: int | None = None
    grid: int = Field(ge=0, le=30)
    position: int | None = Field(default=None, ge=1, le=30)
    position_text: str
    points: float = Field(ge=0, le=60)
    laps_completed: int = Field(ge=0, le=200)
    status: str
    time_millis: int | None = Field(default=None, ge=0)
    fastest_lap_rank: int | None = Field(default=None, ge=1, le=30)
    fastest_lap_millis: int | None = Field(default=None, ge=0)


class QualifyingRow(BronzeRow):
    driver_id: str
    driver_code: str | None = None
    constructor_id: str
    position: int = Field(ge=1, le=30)
    q1_millis: int | None = Field(default=None, ge=0)
    q2_millis: int | None = Field(default=None, ge=0)
    q3_millis: int | None = Field(default=None, ge=0)


class LapRow(BronzeRow):
    driver_id: str
    lap: int = Field(ge=1, le=200)
    position: int = Field(ge=1, le=30)
    time_millis: int = Field(gt=0)


class PitStopRow(BronzeRow):
    driver_id: str
    stop: int = Field(ge=1, le=20)
    lap: int = Field(ge=1, le=200)
    time_of_day: str
    # jolpica ships the odd stop with an empty duration, the lap is still worth keeping
    duration_millis: int | None = Field(default=None, gt=0)


class WeatherRow(BronzeRow):
    circuit_id: str
    observed_at: datetime
    is_forecast: bool
    temperature_c: float = Field(ge=-40, le=60)
    precipitation_mm: float = Field(ge=0, le=200)
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    wind_speed_kph: float = Field(ge=0, le=200)
    relative_humidity: float | None = Field(default=None, ge=0, le=100)


class SessionLapRow(BronzeRow):
    session: SessionKind
    driver_code: str
    driver_number: int
    lap: int = Field(ge=1, le=200)
    lap_time_millis: int | None = Field(default=None, gt=0)
    sector1_millis: int | None = Field(default=None, gt=0)
    sector2_millis: int | None = Field(default=None, gt=0)
    sector3_millis: int | None = Field(default=None, gt=0)
    stint: int | None = Field(default=None, ge=1)
    lap_in_stint: int | None = Field(default=None, ge=1)
    compound: str | None = None
    tyre_life: int | None = Field(default=None, ge=0)
    is_personal_best: bool = False
    is_deleted: bool = False
    is_accurate: bool = False
    # fastf1 ships this as a string of concatenated single-digit flags, not a number
    track_status: str | None = None
    pit_in: bool = False
    pit_out: bool = False
    position: int | None = Field(default=None, ge=1, le=30)


class IncidentRow(BronzeRow):
    document: int | None = None
    # a document ruling on eighteen cars is eighteen rows, and the ordinal keeps them apart
    entry: int = Field(ge=0)
    kind: str
    issued: datetime | None = None
    car: int | None = Field(default=None, ge=0, le=199)
    driver: str | None = None
    competitor: str | None = None
    session: str | None = None
    fact: str | None = None
    charge: str | None = None
    outcome: str | None = None
    reason: str | None = None
    # "parsed" when the field block carried it, "extracted" when a model read the prose
    read_by: str
    # fields a model quoted that were not found in the document, so they are not stored
    unverified: list[str] = []
    raw_key: str


class IncidentSanctionRow(BronzeRow):
    document: int | None = None
    entry: int = Field(ge=0)
    # one decision carries several: a driver penalty, its points and a fine on the team
    ordinal: int = Field(ge=0)
    kind: str
    seconds: int | None = Field(default=None, ge=0)
    positions: int | None = Field(default=None, ge=0)
    points: int | None = Field(default=None, ge=0)
    points_total: int | None = Field(default=None, ge=0)
    amount: int | None = Field(default=None, ge=0)
    currency: str | None = None
    text: str
    raw_key: str


class IncidentArticleRow(BronzeRow):
    document: int | None = None
    entry: int = Field(ge=0)
    code: str
    regulation: str
    book: str | None = None
    edition: int | None = None
    raw_key: str


TABLES: dict[str, type[BronzeRow]] = {
    "races": RaceRow,
    "results": ResultRow,
    "qualifying": QualifyingRow,
    "laps": LapRow,
    "pitstops": PitStopRow,
    "weather": WeatherRow,
    "session_laps": SessionLapRow,
    "incidents": IncidentRow,
    "incident_articles": IncidentArticleRow,
    "incident_sanctions": IncidentSanctionRow,
}


def parse_duration_millis(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lstrip("+")
    if not text:
        return None
    match = DURATION.match(text)
    if not match:
        return None
    hours, minutes, seconds = match.group("h"), match.group("m"), match.group("s")
    if minutes is None:
        hours, minutes = None, hours
    total = int(seconds)
    total += int(minutes or 0) * 60
    total += int(hours or 0) * 3600
    frac = (match.group("frac") or "").ljust(3, "0")[:3]
    return total * 1000 + int(frac or 0)


def _reason_detail(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "row"
    return f"{location}: {first['msg']}"


def validate[Row: BronzeRow](
    table: str, model: type[Row], records: list[dict[str, Any]]
) -> tuple[list[Row], list[Quarantined]]:
    kept: list[Row] = []
    dropped: list[Quarantined] = []
    for record in records:
        try:
            kept.append(model.model_validate(record))
        except ValidationError as exc:
            dropped.append(
                Quarantined(
                    table=table,
                    reason=Reason.CONTRACT,
                    detail=_reason_detail(exc),
                    payload=record,
                )
            )
    return kept, dropped
