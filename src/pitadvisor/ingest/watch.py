import json
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal
from urllib.parse import quote

from pydantic import BaseModel

from pitadvisor.ingest import fia_docs, http, openf1
from pitadvisor.ingest.jolpica import endpoint, races_of
from pitadvisor.ingest.ratelimit import LedgerEntry, RateLimiter
from pitadvisor.ingest.raw_store import ObjectStore, RawStore
from pitadvisor.types import Layer

STATE_KEY: Final = f"{Layer.CACHE}/watch.json"
WORKFLOW: Final = "publish.yml"

# a weekend is watched from the thursday before its race to the tuesday after it
BEFORE: Final = timedelta(days=4)
AFTER: Final = timedelta(days=2)
# a session that never produces data stops being waited on, rather than polled for ever
PATIENCE: Final = timedelta(hours=36)
# the snapshot refreshes the weather and the forecast this long before a session starts
SNAPSHOT_FROM: Final = timedelta(minutes=150)
SNAPSHOT_TO: Final = timedelta(minutes=90)

NAMES: Final = {
    "Sprint Qualifying": "sprint_qualifying",
    "Sprint Shootout": "sprint_qualifying",
    "Sprint": "sprint",
    "Qualifying": "qualifying",
    "Race": "race",
}
# what jolpica calls each classification. sprint qualifying has none, fastf1 is its only source
CLASSIFICATION: Final = {"sprint": "sprint", "qualifying": "qualifying", "race": "results"}
# the sessions whose laps the pipeline ingests through openf1, so race control has to have shown
# the flag first: a session lands in bronze once and is never refetched
ARCHIVED: Final = frozenset({"sprint_qualifying", "sprint", "race"})
SNAPSHOT_BEFORE: Final = frozenset({"sprint", "qualifying", "race"})


class Session(BaseModel, frozen=True):
    kind: str
    starts_at: datetime
    ends_at: datetime
    session_key: int


class Weekend(BaseModel, frozen=True):
    season: int
    round: int
    race_name: str
    race_date: date


class WatchState(BaseModel):
    handled: list[str] = []
    snapshots: list[str] = []
    # fia.com's paths for a season and an event, found once and kept so a poll is one request
    fia_paths: dict[str, str] = {}


class Action(BaseModel, frozen=True):
    mode: Literal["full", "stewards", "snapshot"]
    season: int
    round: int
    reason: str
    mark: str | None = None
    # a full run ingests every session that is in, so the others ready with it are settled too
    also: list[str] = []


class Probes(BaseModel, frozen=True, arbitrary_types_allowed=True):
    sessions: Callable[[Weekend], list[Session]]
    archived: Callable[[Session], bool]
    classified: Callable[[Weekend, str], bool]
    documents: Callable[[Weekend, WatchState], int]


def session_id(weekend: Weekend, kind: str) -> str:
    return f"{weekend.season}-{weekend.round}-{kind}"


def watched(races: list[dict[str, Any]], today: date) -> Weekend | None:
    near = [row for row in races if row["race_date"] - BEFORE <= today <= row["race_date"] + AFTER]
    if not near:
        return None
    row = min(near, key=lambda item: abs((item["race_date"] - today).days))
    return Weekend(
        season=int(row["season"]),
        round=int(row["round"]),
        race_name=str(row["race_name"]),
        race_date=row["race_date"],
    )


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(UTC)


def sessions(listing: list[dict[str, Any]], race_date: date) -> list[Session]:
    """openf1 groups a weekend by meeting, and the race's day is the one thing it shares with
    jolpica's calendar: the two spell race names differently."""
    meetings = {
        item.get("meeting_key")
        for item in listing
        if item.get("session_name") == "Race"
        and abs((_utc(str(item["date_start"])).date() - race_date).days) <= 1
    }
    found = [
        Session(
            kind=NAMES[str(item["session_name"])],
            starts_at=_utc(str(item["date_start"])),
            ends_at=_utc(str(item["date_end"])),
            session_key=int(item["session_key"]),
        )
        for item in listing
        if item.get("meeting_key") in meetings
        and str(item.get("session_name")) in NAMES
        and not item.get("is_cancelled")
    ]
    return sorted(found, key=lambda session: session.starts_at)


def due(now: datetime, weekend: Weekend, state: WatchState, probes: Probes) -> Action | None:
    """One action a poll, most valuable first: a session's data, then a new stewards' document,
    then the weather before the next session. Two runs at once would race to write the views."""
    clock = probes.sessions(weekend)

    def ready(session: Session) -> bool:
        mark = session_id(weekend, session.kind)
        if mark in state.handled or now < session.ends_at or now > session.ends_at + PATIENCE:
            return False
        if session.kind in ARCHIVED and not probes.archived(session):
            return False
        resource = CLASSIFICATION.get(session.kind)
        return resource is None or probes.classified(weekend, resource)

    landed = [session for session in clock if ready(session)]
    if landed:
        first, *rest = landed
        return Action(
            mode="full",
            season=weekend.season,
            round=weekend.round,
            reason=f"{first.kind} is in",
            mark=session_id(weekend, first.kind),
            also=[session_id(weekend, session.kind) for session in rest],
        )
    pending = probes.documents(weekend, state)
    if pending:
        return Action(
            mode="stewards",
            season=weekend.season,
            round=weekend.round,
            reason=f"{pending} new stewards' documents",
        )
    for session in clock:
        mark = session_id(weekend, session.kind)
        if session.kind not in SNAPSHOT_BEFORE or mark in state.snapshots:
            continue
        if session.starts_at - SNAPSHOT_FROM <= now < session.starts_at - SNAPSHOT_TO:
            return Action(
                mode="snapshot",
                season=weekend.season,
                round=weekend.round,
                reason=f"weather before {session.kind}",
                mark=mark,
            )
    return None


def record(state: WatchState, action: Action) -> WatchState:
    if action.mark is None:
        return state
    if action.mode == "full":
        return state.model_copy(update={"handled": [*state.handled, action.mark, *action.also]})
    return state.model_copy(update={"snapshots": [*state.snapshots, action.mark]})


def load(store: ObjectStore) -> WatchState:
    if not store.exists(STATE_KEY):
        return WatchState()
    return WatchState.model_validate_json(store.get(STATE_KEY))


def save(store: ObjectStore, state: WatchState) -> None:
    store.put(STATE_KEY, state.model_dump_json(indent=2).encode())


class Unrecorded:
    """A probe asks whether something exists, it does not fetch it. Writing its etag to the
    real ledger would turn the pipeline's later fetch of the same url into a 304 for a payload
    raw/ never received."""

    def lookup(self, url: str) -> LedgerEntry | None:
        return None

    def record(self, entry: LedgerEntry) -> None:
        return None


def _json(response: http.Response) -> Any:
    return json.loads(response.body.decode("utf-8-sig") or "null")


def live_probes(
    store: ObjectStore,
    timing: RateLimiter,
    jolpica: RateLimiter,
    fia: RateLimiter,
    fetch: Callable[..., http.Response] = http.fetch,
    sleep: Callable[[float], None] = time.sleep,
    clock_now: Callable[[], float] = time.monotonic,
) -> Probes:
    probe = Unrecorded()
    last_fia = [clock_now() - fia_docs.FIA_PAUSE_SECONDS]

    def get(
        url: str, limiter: RateLimiter, accept: str = "application/json"
    ) -> http.Response | None:
        try:
            return fetch(url, probe, limiter, accept=accept)
        except (http.FetchError, OSError):
            return None

    def page(path: str) -> str | None:
        # fia.com's crawl delay holds between the three requests a first look can take
        waited = clock_now() - last_fia[0]
        if waited < fia_docs.FIA_PAUSE_SECONDS:
            sleep(fia_docs.FIA_PAUSE_SECONDS - waited)
        last_fia[0] = clock_now()
        found = get(fia_docs.BASE + quote(path, safe="/:"), fia, accept="text/html")
        return found.body.decode("utf-8", "replace") if found else None

    def clock(weekend: Weekend) -> list[Session]:
        found = get(openf1.url("sessions", year=weekend.season), timing)
        return sessions(_json(found) or [], weekend.race_date) if found else []

    def archived(session: Session) -> bool:
        found = get(openf1.url("race_control", session_key=session.session_key), timing)
        return bool(found) and openf1.finished(_json(found) or [])

    def classified(weekend: Weekend, resource: str) -> bool:
        found = get(endpoint(weekend.season, weekend.round, resource), jolpica)
        if not found or found.status != 200:
            return False
        key = {"results": "Results", "qualifying": "QualifyingResults", "sprint": "SprintResults"}
        return any(race.get(key[resource]) for race in races_of(_json(found)))

    def documents(weekend: Weekend, state: WatchState) -> int:
        event_path = _fia_event(weekend, state)
        if event_path is None:
            return 0
        html = page(event_path)
        if html is None:
            return 0
        event = event_path.rsplit("/event/", 1)[1]
        return len(fia_docs.pending(RawStore(store), html, weekend.season, event, weekend.round))

    def _fia_event(weekend: Weekend, state: WatchState) -> str | None:
        known = f"{weekend.season}-{weekend.round}"
        if known in state.fia_paths:
            return state.fia_paths[known]
        season_key = str(weekend.season)
        if season_key not in state.fia_paths:
            listing = page(fia_docs.CHAMPIONSHIP)
            paths = fia_docs.seasons(listing) if listing else {}
            if weekend.season not in paths:
                return None
            state.fia_paths[season_key] = paths[weekend.season]
        listing = page(state.fia_paths[season_key])
        if listing is None:
            return None
        for event in fia_docs.events(listing):
            if fia_docs.round_of(event, {weekend.race_name: weekend.round}) == weekend.round:
                state.fia_paths[known] = f"{state.fia_paths[season_key]}/event/{event}"
                return state.fia_paths[known]
        return None

    return Probes(sessions=clock, archived=archived, classified=classified, documents=documents)


def execution(action: Action, now: datetime) -> tuple[str, dict[str, Any]]:
    name = f"watch-{action.mode}-{action.season}-{action.round}-{now.strftime('%Y%m%dT%H%M%S')}"
    if action.mode == "full":
        # the weekend machine plans for itself, force only skips the day-of-week gate
        return name, {"force": True}
    return name, {"mode": action.mode, "season": action.season, "round": action.round}


def poll(
    now: datetime,
    races: list[dict[str, Any]],
    store: ObjectStore,
    probes: Probes,
    running: Callable[[], bool],
    start: Callable[[Action], None],
) -> Action | None:
    weekend = watched(races, now.date())
    if weekend is None or running():
        return None
    state = load(store)
    action = due(now, weekend, state, probes)
    if action is not None:
        start(action)
        state = record(state, action)
    save(store, state)
    return action


Opener = Callable[[urllib.request.Request, float], Any]


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def dispatch(repo: str, token: str, opener: Opener = _open) -> int:
    """Starts the publish workflow on main: the site is a static export, so new views only reach
    it when it is built again. A workflow dispatch needs a token that can run actions and
    nothing else, where a repository dispatch would need one that can push code."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches",
        data=json.dumps({"ref": "main"}).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pit-advisor-watch",
        },
    )
    with opener(request, 30.0) as response:
        return int(response.status)
