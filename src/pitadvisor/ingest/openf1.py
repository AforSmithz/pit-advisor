import json
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, cast

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import ObjectStore, RawStore, write_bronze, write_quarantine
from pitadvisor.quality import contracts
from pitadvisor.types import (
    IngestOutcome,
    Provenance,
    SeasonKey,
    SessionKey,
    SessionKind,
    Source,
    bronze_key,
)

BASE: Final = "https://api.openf1.org/v1"
# the free tier allows 30 requests a minute, so one every two seconds stays inside it
PAUSE_SECONDS: Final = 2.0
# one raw object each, all of them needed to build a session's laps
PARTS: Final = ("session", "drivers", "laps", "stints", "pit", "race_control", "position")
# a session's own row comes from the listing endpoint, filtered to the one key
ENDPOINTS: Final = {"session": "sessions"}

KINDS: Final[dict[str, SessionKind]] = {
    "Practice 1": SessionKind.FP1,
    "Practice 2": SessionKind.FP2,
    "Practice 3": SessionKind.FP3,
    "Sprint Qualifying": SessionKind.SPRINT_QUALIFYING,
    "Sprint Shootout": SessionKind.SPRINT_SHOOTOUT,
    "Sprint": SessionKind.SPRINT,
    "Qualifying": SessionKind.QUALIFYING,
    "Race": SessionKind.RACE,
}
# fastf1 only gives a running order in the sessions that have one
RANKED: Final = frozenset({SessionKind.SPRINT, SessionKind.RACE})

# fastf1's track status digits, so everything downstream of bronze reads both sources alike
GREEN, YELLOW, SAFETY_CAR, RED, VSC, VSC_ENDING = "1", "2", "4", "5", "6", "7"

DELETION = re.compile(
    r"CAR (?P<car>\d+) \(\w+\) (?:TIME (?P<time>[\d:.]+)|LAP) (?P<verdict>DELETED|REINSTATED)"
    r"(?:.*?\bLAP (?P<lap>\d+)\b)?"
)


def url(path: str, **query: int | str) -> str:
    joined = "&".join(f"{name}={value}" for name, value in query.items())
    return f"{BASE}/{path}?{joined}"


def _at(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp).astimezone(UTC) if stamp else None


def _millis(seconds: float | None) -> int | None:
    if seconds is None or seconds <= 0:
        return None
    return round(seconds * 1000)


def finished(race_control: list[dict[str, Any]]) -> bool:
    return any(
        item.get("flag") == "CHEQUERED" or item.get("message") == "SESSION FINISHED"
        for item in race_control
    )


def scheduled(
    listing: list[dict[str, Any]], races: dict[int, date], kinds: Iterable[SessionKind]
) -> list[tuple[int, SessionKind, dict[str, Any]]]:
    """openf1 knows meetings, not rounds. The race session's day is the one thing it shares
    with jolpica's calendar, a day either way for the night races that end after midnight."""
    wanted = set(kinds)
    rounds: dict[int, int] = {}
    for item in listing:
        if item.get("session_name") != "Race" or item.get("is_cancelled"):
            continue
        day = cast(datetime, _at(item.get("date_start"))).date()
        for round_, race_date in races.items():
            if abs((race_date - day).days) <= 1:
                rounds[int(item["meeting_key"])] = round_
    found: list[tuple[int, SessionKind, dict[str, Any]]] = []
    for item in listing:
        kind = KINDS.get(str(item.get("session_name")))
        round_ = rounds.get(int(item.get("meeting_key") or 0))
        if kind is None or round_ is None or kind not in wanted or item.get("is_cancelled"):
            continue
        found.append((round_, kind, item))
    return sorted(found, key=lambda entry: (entry[0], str(entry[2]["date_start"])))


def _clock_millis(text: str) -> int:
    minutes, _, seconds = text.rpartition(":")
    return round((int(minutes or 0) * 60 + float(seconds)) * 1000)


def _deleted(
    race_control: list[dict[str, Any]], times: dict[tuple[int, int], int | None]
) -> set[tuple[int, int]]:
    """A deletion names the time and a lap. The time wins: openf1 numbered one 2026 race a lap
    behind race control, and the time is the same under either count."""

    def lap_of(car: int, time_text: str | None, lap_text: str | None) -> tuple[int, int] | None:
        if time_text is not None:
            wanted = _clock_millis(time_text)
            for (driver, number), millis in times.items():
                if driver == car and millis is not None and abs(millis - wanted) <= 1:
                    return (car, number)
        return (car, int(lap_text)) if lap_text else None

    deleted: set[tuple[int, int]] = set()
    for item in sorted(race_control, key=lambda entry: str(entry.get("date"))):
        matched = DELETION.search(str(item.get("message") or ""))
        if matched is None:
            continue
        found = lap_of(int(matched.group("car")), matched.group("time"), matched.group("lap"))
        if found is None:
            continue
        if matched.group("verdict") == "DELETED":
            deleted.add(found)
        else:
            deleted.discard(found)
    return deleted


def _stints(
    laps: list[dict[str, Any]], published: list[dict[str, Any]]
) -> dict[tuple[int, int], tuple[int, int, dict[str, Any] | None]]:
    """(stint, lap in stint, the published stint it ran) per car and lap. A stint starts on an
    out-lap, as fastf1 counts it: openf1's own lap_start is sometimes a lap early, on the in-lap,
    so its stints only give the compound and the tyre's age, matched at the stint's middle lap."""
    by_car: dict[int, list[int]] = {}
    out_laps: set[tuple[int, int]] = set()
    for item in laps:
        car, number = int(item["driver_number"]), int(item["lap_number"])
        by_car.setdefault(car, []).append(number)
        if item.get("is_pit_out_lap") and number > 1:
            out_laps.add((car, number))
    found: dict[tuple[int, int], tuple[int, int, dict[str, Any] | None]] = {}
    for car, numbers in by_car.items():
        runs: list[list[int]] = []
        for number in sorted(numbers):
            if not runs or (car, number) in out_laps:
                runs.append([])
            runs[-1].append(number)
        mine = [item for item in published if int(item["driver_number"]) == car]
        for stint, run in enumerate(runs, start=1):
            middle = run[len(run) // 2]
            match = next(
                (
                    item
                    for item in mine
                    if int(item.get("lap_start") or 0) <= middle <= int(item.get("lap_end") or 0)
                ),
                next((item for item in mine if item.get("stint_number") == stint), None),
            )
            for offset, number in enumerate(run, start=1):
                found[(car, number)] = (stint, offset, match)
    return found


def track_timeline(
    race_control: list[dict[str, Any]], lap_starts: dict[int, datetime]
) -> list[tuple[datetime, str]]:
    """Every change of track status. race control says when a safety car comes in, not when
    the green flag shows, so a safety car ends where the leader starts the next lap."""
    changes: list[tuple[datetime, str]] = []
    neutral: str | None = None
    yellows: set[str] = set()
    ending: datetime | None = None

    def status() -> str:
        return neutral or (YELLOW if yellows else GREEN)

    def mark(at: datetime) -> None:
        current = status()
        if not changes or changes[-1][1] != current:
            changes.append((at, current))

    for item in sorted(race_control, key=lambda entry: str(entry.get("date"))):
        at = _at(item.get("date"))
        if at is None:
            continue
        if ending is not None and at >= ending:
            restarted, neutral, ending = ending, None, None
            mark(restarted)
        message = str(item.get("message") or "")
        flag = item.get("flag")
        scope = item.get("scope")
        if item.get("category") == "SafetyCar":
            if message.startswith("VSC DEPLOYED") or "VIRTUAL SAFETY CAR DEPLOYED" in message:
                neutral = VSC
            elif message.startswith("VSC ENDING") or "VIRTUAL SAFETY CAR ENDING" in message:
                neutral = VSC_ENDING
            elif "SAFETY CAR DEPLOYED" in message:
                neutral = SAFETY_CAR
            elif "SAFETY CAR IN THIS LAP" in message and item.get("lap_number"):
                ending = lap_starts.get(int(item["lap_number"]) + 1)
        elif flag == "RED" and scope == "Track":
            neutral = RED
        elif flag in ("CLEAR", "GREEN") and scope == "Track":
            neutral, ending = None, None
            yellows.clear()
        elif flag in ("YELLOW", "DOUBLE YELLOW"):
            yellows.add(str(item.get("sector") if scope == "Sector" else "track"))
        elif flag == "CLEAR" and scope == "Sector":
            yellows.discard(str(item.get("sector")))
        mark(at)
    if ending is not None:
        neutral = None
        mark(ending)
    return changes


def status_during(timeline: list[tuple[datetime, str]], start: datetime, end: datetime) -> str:
    seen = [GREEN]
    for at, code in timeline:
        if at <= start:
            seen = [code]
        elif at < end and seen[-1] != code:
            seen.append(code)
    return "".join(seen)


def _position_at(moves: list[tuple[datetime, int]], when: datetime) -> int | None:
    found: int | None = None
    for at, position in moves:
        if at > when:
            break
        found = position
    return found


def to_records(
    bundle: dict[str, Any], key: SessionKey, stamp: dict[str, Any]
) -> list[dict[str, Any]]:
    session = cast(list[dict[str, Any]], bundle["session"])
    zero = _at(session[0].get("date_start")) if session else None
    codes = {
        int(item["driver_number"]): str(item.get("name_acronym") or "")
        for item in cast(list[dict[str, Any]], bundle["drivers"])
    }
    laps = sorted(
        cast(list[dict[str, Any]], bundle["laps"]),
        key=lambda item: (int(item["driver_number"]), int(item["lap_number"])),
    )
    stints = _stints(laps, cast(list[dict[str, Any]], bundle["stints"]))
    pitted = {
        (int(item["driver_number"]), int(item["lap_number"]))
        for item in cast(list[dict[str, Any]], bundle["pit"])
        if item.get("lap_number") is not None
    }
    moves: dict[int, list[tuple[datetime, int]]] = {}
    for item in cast(list[dict[str, Any]], bundle["position"]):
        at = _at(item.get("date"))
        if at is not None and item.get("position") is not None:
            moves.setdefault(int(item["driver_number"]), []).append((at, int(item["position"])))
    for driver_moves in moves.values():
        driver_moves.sort()
    race_control = cast(list[dict[str, Any]], bundle["race_control"])
    deleted = _deleted(
        race_control,
        {
            (int(item["driver_number"]), int(item["lap_number"])): _millis(item.get("lap_duration"))
            for item in laps
        },
    )

    lap_starts: dict[int, datetime] = {}
    starts: dict[tuple[int, int], datetime] = {}
    for item in laps:
        at = _at(item.get("date_start"))
        if at is None:
            continue
        starts[(int(item["driver_number"]), int(item["lap_number"]))] = at
        number = int(item["lap_number"])
        lap_starts[number] = min(at, lap_starts.get(number, at))
    timeline = track_timeline(race_control, lap_starts)

    records: list[dict[str, Any]] = []
    best: dict[int, int] = {}
    for item in laps:
        car = int(item["driver_number"])
        number = int(item["lap_number"])
        lap_time = _millis(item.get("lap_duration"))
        sectors = [_millis(item.get(f"duration_sector_{index}")) for index in (1, 2, 3)]
        started = starts.get((car, number))
        ended = (
            started + timedelta(milliseconds=lap_time)
            if started is not None and lap_time is not None
            else starts.get((car, number + 1))
        )
        stint_number, lap_in_stint, stint = stints[(car, number)]
        compound = str(stint.get("compound") or "") if stint else ""
        pit_out = bool(item.get("is_pit_out_lap"))
        pit_in = (car, number) in pitted
        is_deleted = (car, number) in deleted
        timed = lap_time is not None and all(sector is not None for sector in sectors)
        agrees = timed and abs(sum(cast(list[int], sectors)) - cast(int, lap_time)) <= 100
        personal_best = False
        if lap_time is not None and not is_deleted and lap_time < best.get(car, 1 << 31):
            best[car] = lap_time
            personal_best = True
        clock = (
            round((ended - zero).total_seconds() * 1000)
            if ended is not None and zero is not None
            else None
        )
        records.append(
            {
                **stamp,
                "season": key.season,
                "round": key.round,
                "session": key.session,
                "driver_code": codes.get(car, ""),
                "driver_number": car,
                "lap": number,
                "lap_time_millis": lap_time,
                "sector1_millis": sectors[0],
                "sector2_millis": sectors[1],
                "sector3_millis": sectors[2],
                "stint": stint_number,
                "lap_in_stint": lap_in_stint,
                "compound": compound if compound and compound != "UNKNOWN" else None,
                "tyre_life": (
                    int(stint.get("tyre_age_at_start") or 0) + lap_in_stint if stint else None
                ),
                "is_personal_best": personal_best,
                "is_deleted": is_deleted,
                # fastf1 calls a lap accurate when its timing adds up and it is a flying lap
                "is_accurate": agrees and number > 1 and not pit_in and not pit_out,
                "track_status": (
                    status_during(timeline, started, ended)
                    if started is not None and ended is not None
                    else None
                ),
                "pit_in": pit_in,
                "pit_out": pit_out,
                "position": (
                    _position_at(moves.get(car, []), ended)
                    if key.session in RANKED and ended is not None
                    else None
                ),
                "session_time_millis": clock if clock is not None and clock > 0 else None,
            }
        )
    return records


class OpenF1Client:
    def __init__(
        self,
        raw: RawStore,
        ledger: Ledger,
        limiter: RateLimiter | None = None,
        run_id: str = "local",
        fetch: Callable[..., http.Response] | None = None,
        pause_seconds: float = PAUSE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.raw = raw
        # openf1 sends no etags, and a 304 would point at a raw copy that may not exist
        self.ledger = http.Unconditional(ledger)
        self.limiter = limiter
        self.run_id = run_id
        self.fetch = fetch or http.fetch
        self.pause_seconds = pause_seconds
        self.sleep = sleep
        self.requests = 0

    def read(self, target: str) -> http.Response:
        if self.requests:
            self.sleep(self.pause_seconds)
        self.requests += 1
        return self.fetch(target, self.ledger, self.limiter)

    def land(self, key: SeasonKey, name: str, response: http.Response) -> str:
        return self.raw.land(
            key,
            name,
            response.body,
            Provenance(
                run_id=self.run_id,
                source=Source.OPENF1,
                url=response.url,
                fetched_at=response.fetched_at,
                status=response.status,
            ),
        )

    def sessions(self, season: int) -> list[dict[str, Any]]:
        response = self.read(url("sessions", year=season))
        self.land(SeasonKey(season=season), "sessions", response)
        return cast(list[dict[str, Any]], json.loads(response.body or b"[]"))


def ingest_session(
    client: OpenF1Client, store: ObjectStore, key: SessionKey, session_key: int
) -> IngestOutcome:
    def skipped(reason: str) -> IngestOutcome:
        return IngestOutcome(
            source=Source.OPENF1,
            table="session_laps",
            season=key.season,
            round=key.round,
            requests=client.requests - spent,
            skipped=reason,
        )

    spent = client.requests
    # race control first: nothing lands until the session is over, so raw never holds half
    # a session that a rebuild would then turn into bronze
    responses = {"race_control": client.read(url("race_control", session_key=session_key))}
    if not finished(json.loads(responses["race_control"].body or b"[]")):
        return skipped(f"{key.session} not finished upstream")
    for part in PARTS:
        if part not in responses:
            responses[part] = client.read(url(ENDPOINTS.get(part, part), session_key=session_key))
    bundle = {part: json.loads(response.body or b"[]") for part, response in responses.items()}
    if not bundle["laps"]:
        return skipped(f"{key.session} has no laps upstream")
    landed = [client.land(key, part, responses[part]) for part in PARTS]
    stamp = {"run_id": client.run_id, "ingested_at": datetime.now(UTC)}
    kept, dropped = contracts.validate(
        "session_laps", contracts.SessionLapRow, to_records(bundle, key, stamp)
    )
    write_quarantine(store, "session_laps", key, client.run_id, dropped)
    return IngestOutcome(
        source=Source.OPENF1,
        table="session_laps",
        season=key.season,
        round=key.round,
        rows=len(kept),
        quarantined=len(dropped),
        raw_objects=landed,
        bronze_objects=[write_bronze(store, "session_laps", key, kept)] if kept else [],
        requests=client.requests - spent,
    )


def backfill(
    client: OpenF1Client,
    store: ObjectStore,
    season: int,
    races: dict[int, date],
    kinds: Iterable[SessionKind] = (SessionKind.RACE,),
    now: datetime | None = None,
    skip_present: bool = True,
) -> list[IngestOutcome]:
    if not races:
        return []
    moment = now or datetime.now(UTC)
    outcomes: list[IngestOutcome] = []
    for round_, kind, item in scheduled(client.sessions(season), races, kinds):
        ended = _at(item.get("date_end"))
        if ended is None or ended > moment:
            continue
        key = SessionKey(season=season, round=round_, session=kind)
        if skip_present and store.exists(bronze_key("session_laps", key)):
            outcomes.append(
                IngestOutcome(
                    source=Source.OPENF1,
                    table="session_laps",
                    season=season,
                    round=round_,
                    skipped="already in bronze",
                )
            )
            continue
        outcomes.append(ingest_session(client, store, key, int(item["session_key"])))
    return outcomes


def location_url(session_key: int, driver: int, start: datetime, end: datetime) -> str:
    stamp = "%Y-%m-%dT%H:%M:%S.%f"
    return (
        f"{BASE}/location?session_key={session_key}&driver_number={driver}"
        f"&date>{start.astimezone(UTC).strftime(stamp)}&date<{end.astimezone(UTC).strftime(stamp)}"
    )


def fastest_lap(laps: list[dict[str, Any]]) -> dict[str, Any] | None:
    timed = [
        lap
        for lap in laps
        if lap.get("lap_duration")
        and lap.get("date_start")
        and lap.get("duration_sector_1")
        and lap.get("duration_sector_2")
        and not lap.get("is_pit_out_lap")
    ]
    return min(timed, key=lambda lap: float(lap["lap_duration"])) if timed else None


def _held_or_read(client: OpenF1Client, key: SessionKey, name: str, target: str) -> bytes:
    held = client.raw.latest(Source.OPENF1, key, name)
    if held is not None:
        return held[0]
    response = client.read(target)
    client.land(key, name, response)
    return response.body


def lap_trace(
    client: OpenF1Client, season: int, round_: int, race_date: date, now: datetime
) -> tuple[SessionKey, dict[str, Any], list[dict[str, Any]]] | None:
    """The fastest qualifying lap of one event and the car's position samples over it."""
    for _, _, item in scheduled(
        client.sessions(season), {round_: race_date}, (SessionKind.QUALIFYING,)
    ):
        ended = _at(item.get("date_end"))
        if ended is None or ended > now:
            return None
        key = SessionKey(season=season, round=round_, session=SessionKind.QUALIFYING)
        session_key = int(item["session_key"])
        laps = json.loads(
            _held_or_read(client, key, "laps", url("laps", session_key=session_key)) or b"[]"
        )
        best = fastest_lap(laps)
        if best is None:
            return None
        driver = int(best["driver_number"])
        start = cast(datetime, _at(best["date_start"]))
        end = start + timedelta(seconds=float(best["lap_duration"]) + 1)
        # two digits in the name, so car 4's prefix never picks up car 44's object
        trace = _held_or_read(
            client, key, f"location-car{driver:02d}", location_url(session_key, driver, start, end)
        )
        return key, best, json.loads(trace or b"[]")
    return None
