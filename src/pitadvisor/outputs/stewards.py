# pyright: reportUnknownMemberType=false
import re
from datetime import UTC, datetime
from typing import Final, cast

import polars as pl
from pydantic import BaseModel

from pitadvisor.features.assemble import EventContext
from pitadvisor.features.grid import seated
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION

KEYS: Final = ["season", "round", "document", "entry"]

# the rule a decision is filed under, normalised so "2.5.5b" and "2.5.5 b)" are one rule
OFFENCES: Final = {
    "appendixl,chapteriv,article2d": ("collision", "Causing a collision"),
    "appendixl,chapteriv,article2b": ("leaving", "Leaving the track for an advantage"),
    "article37.5": ("impeding", "Impeding"),
    "appendixh,article2.5.5b": ("yellow", "Yellow flags"),
    "article12.2.1i": ("procedure", "Track and procedure"),
    "article12.2.1": ("conduct", "Driving standards"),
    "article34.7": ("pitlane", "Pit lane speeding"),
}
LABELS: Final = dict(OFFENCES.values())
# a decision that cites nothing is still about something, and the fact usually says what
WORDS: Final = (
    (re.compile(r"\bimped", re.I), "impeding"),
    (re.compile(r"\bcollid|\bcollision", re.I), "collision"),
    (re.compile(r"\byellow", re.I), "yellow"),
    (re.compile(r"pit\s*lane\s*speed", re.I), "pitlane"),
)
OUTCOMES: Final = ("none", "warning", "fine", "time", "drive_through", "grid")
BUCKET: Final = {
    "none": "none",
    "warning": "warning",
    "reprimand": "warning",
    "fine": "fine",
    "time_penalty": "time",
    "drive_through": "drive_through",
    "stop_and_go": "drive_through",
    "grid_drop": "grid",
    "back_of_grid": "grid",
    "pit_lane_start": "grid",
}


class Sanction(BaseModel, frozen=True):
    kind: str
    outcome: str
    seconds: int | None = None
    positions: int | None = None
    text: str = ""


class Ruling(BaseModel, frozen=True):
    document: int
    entry: int
    kind: str
    issued: datetime | None
    session: str | None
    car: int | None
    driver: str | None
    driver_code: str | None
    competitor: str | None
    fact: str | None
    outcome: str | None
    rule: str | None
    offence: str | None
    sanction: Sanction


class Precedent(BaseModel, frozen=True):
    offence: str
    label: str
    rulings: int
    outcomes: dict[str, int]
    # a time penalty is the one outcome whose size varies enough to be worth splitting
    seconds: dict[str, int]


class TurnIncident(BaseModel, frozen=True):
    season: int
    session: str
    lap: int | None
    kind: str
    drivers: list[str]
    offence: str | None
    # the last thing race control said about it, or the penalty it announced
    outcome: str


class TurnRecord(BaseModel, frozen=True):
    number: int
    track_limits: int
    off_track: int
    incidents: list[TurnIncident]


class StewardsView(BaseModel, frozen=True):
    view: str = "stewards_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    event: EventContext
    rulings: list[Ruling]
    from_season: int | None
    total_rulings: int
    precedents: list[Precedent]
    # race control at this circuit, by the turn it names
    turn_seasons: list[int] = []
    turns: list[TurnRecord] = []


DRIVER = re.compile(r"\b\d+ \((?P<code>[A-Z]{3})\)")
DELETED_AT = re.compile(
    r"\bDELETED - TRACK LIMITS AT TURN (?P<turn>\d+)|\bTRACK LIMITS TURN (?P<bare>\d+)"
)
OFF_AT = re.compile(r"\b(?:OFF TRACK|SPUN) AND CONTINUED AT TURN (?P<turn>\d+)")
INCIDENT = re.compile(
    r"^(?:FIA STEWARDS: )?(?:LAP (?P<lap>\d+) )?(?:BETWEEN )?TURNS? (?P<turn>\d+)(?: AND \d+)?"
    r"(?: INCIDENT)?(?: INVOLVING CARS? (?P<cars>.+?))? "
    r"(?P<status>NOTED|UNDER INVESTIGATION|REVIEWED NO FURTHER INVESTIGATION|NO FURTHER ACTION"
    r"|NO FURTHER INVESTIGATION|WILL BE INVESTIGATED AFTER THE \w+)(?: - (?P<offence>.+?))?\s*$"
)
PENALTY = re.compile(
    r"^FIA STEWARDS: (?P<penalty>.+? PENALTY) FOR CAR \d+ \((?P<code>[A-Z]{3})\)"
    r" - (?P<offence>.+?)\s*$"
)
INCIDENT_KINDS: Final = (
    ("CAUSING A COLLISION", "collision"),
    ("IMPEDING", "impeding"),
    ("LEAVING THE TRACK", "leaving"),
    ("FORCING ANOTHER DRIVER", "forcing"),
)


def _incident_kind(offence: str | None) -> str:
    return next((kind for text, kind in INCIDENT_KINDS if offence and text in offence), "other")


def _sentence(text: str) -> str:
    return text[:1] + text[1:].lower()


def turn_history(
    race_control: pl.DataFrame | None, races: pl.DataFrame | None, circuit_id: str
) -> tuple[list[int], list[TurnRecord]]:
    """What race control said at each numbered turn of one circuit, over every session held."""
    if race_control is None or races is None or not race_control.height:
        return [], []
    here = races.filter(pl.col("circuit_id") == circuit_id).select(
        pl.col("season").cast(pl.Int64), pl.col("round").cast(pl.Int64)
    )
    messages = race_control.with_columns(
        pl.col("season").cast(pl.Int64), pl.col("round").cast(pl.Int64)
    ).join(here, on=["season", "round"])
    if not messages.height:
        return [], []
    limits: dict[int, int] = {}
    offs: dict[int, int] = {}
    # one incident is a run of messages, so it is keyed by what stays the same across them
    incidents: dict[tuple[object, ...], dict[str, object]] = {}
    for row in messages.sort(["season", "round", "session", "ordinal"]).iter_rows(named=True):
        text = str(row["message"])
        if found := DELETED_AT.search(text):
            turn = int(found.group("turn") or found.group("bare"))
            limits[turn] = limits.get(turn, 0) + 1
            continue
        if found := OFF_AT.search(text):
            turn = int(found.group("turn"))
            offs[turn] = offs.get(turn, 0) + 1
            continue
        session = (row["season"], row["round"], row["session"])
        if found := INCIDENT.match(text):
            drivers = sorted(DRIVER.findall(found.group("cars") or ""))
            offence = found.group("offence")
            key = (*session, int(found.group("turn")), *drivers, offence)
            lap = found.group("lap") or row["lap"]
            held = incidents.setdefault(
                key,
                {
                    "turn": int(found.group("turn")),
                    "season": row["season"],
                    "session": row["session"],
                    "lap": int(lap) if lap is not None else None,
                    "drivers": drivers,
                    "offence": offence,
                },
            )
            held["outcome"] = _sentence(found.group("status"))
            continue
        if found := PENALTY.match(text):
            for key, held in reversed(incidents.items()):
                if (
                    key[:3] == session
                    and found.group("code") in cast(list[str], held["drivers"])
                    and held["offence"] == found.group("offence")
                ):
                    held["outcome"] = (
                        f"{_sentence(found.group('penalty'))} for {found.group('code')}"
                    )
                    break
    by_turn: dict[int, list[TurnIncident]] = {}
    for held in incidents.values():
        offence = cast(str | None, held["offence"])
        by_turn.setdefault(cast(int, held["turn"]), []).append(
            TurnIncident(
                season=cast(int, held["season"]),
                session=str(held["session"]),
                lap=cast(int | None, held["lap"]),
                kind=_incident_kind(offence),
                drivers=cast(list[str], held["drivers"]),
                offence=_sentence(offence) if offence else None,
                outcome=str(held["outcome"]),
            )
        )
    numbers = sorted(set(limits) | set(offs) | set(by_turn))
    records = [
        TurnRecord(
            number=number,
            track_limits=limits.get(number, 0),
            off_track=offs.get(number, 0),
            incidents=by_turn.get(number, []),
        )
        for number in numbers
    ]
    return sorted(int(season) for season in messages["season"].unique()), records


def _rule_key(code: str | None) -> str | None:
    if not code:
        return None
    return re.sub(r"[\s()]", "", code).lower()


def offence_of(code: str | None, fact: str | None) -> str | None:
    found = OFFENCES.get(_rule_key(code) or "")
    if found is not None:
        return found[0]
    for pattern, offence in WORDS:
        if fact and pattern.search(fact):
            return offence
    return None


def _first_articles(articles: pl.DataFrame | None) -> pl.DataFrame:
    if articles is None or not articles.height:
        return pl.DataFrame(schema={**dict.fromkeys(KEYS, pl.Int64), "code": pl.String})
    return (
        articles.sort([*KEYS, "book", "code"])
        .group_by(KEYS, maintain_order=True)
        .first()
        .select(*[pl.col(key).cast(pl.Int64) for key in KEYS], pl.col("code").cast(pl.String))
    )


def _first_sanctions(sanctions: pl.DataFrame | None) -> pl.DataFrame:
    schema = {
        **dict.fromkeys(KEYS, pl.Int64),
        "sanction_kind": pl.String,
        "seconds": pl.Int64,
        "positions": pl.Int64,
        "sanction_text": pl.String,
    }
    if sanctions is None or not sanctions.height:
        return pl.DataFrame(schema=schema)
    return sanctions.filter(pl.col("ordinal") == 0).select(
        *[pl.col(key).cast(pl.Int64) for key in KEYS],
        pl.col("kind").cast(pl.String).alias("sanction_kind"),
        pl.col("seconds").cast(pl.Int64),
        pl.col("positions").cast(pl.Int64),
        pl.col("text").cast(pl.String).alias("sanction_text"),
    )


def rulings_frame(
    incidents: pl.DataFrame,
    sanctions: pl.DataFrame | None,
    articles: pl.DataFrame | None,
    results: pl.DataFrame,
) -> pl.DataFrame:
    """One row per ruling about a driver, with its first cited rule, its first sanction and the
    driver code the car carried that weekend."""
    named = incidents.filter(pl.col("driver").is_not_null()).with_columns(
        *[pl.col(key).cast(pl.Int64) for key in KEYS]
    )
    joined = named.join(_first_articles(articles), on=KEYS, how="left").join(
        _first_sanctions(sanctions), on=KEYS, how="left"
    )
    return seated(joined, results).with_columns(
        pl.struct("code", "fact")
        .map_elements(lambda row: offence_of(row["code"], row["fact"]), return_dtype=pl.String)
        .alias("offence"),
        pl.col("sanction_kind")
        .fill_null("none")
        .replace_strict(BUCKET, default="other", return_dtype=pl.String)
        .alias("bucket"),
    )


def precedents(frame: pl.DataFrame) -> list[Precedent]:
    found: list[Precedent] = []
    for (offence,), group in frame.drop_nulls("offence").group_by("offence"):
        counts = dict(group.group_by("bucket").len().iter_rows())
        timed = (
            group.filter(pl.col("bucket") == "time")
            .drop_nulls("seconds")
            .group_by("seconds")
            .len()
            .sort("seconds")
        )
        found.append(
            Precedent(
                offence=str(offence),
                label=LABELS[str(offence)],
                rulings=group.height,
                outcomes={name: int(counts.get(name, 0)) for name in OUTCOMES},
                seconds={str(seconds): int(count) for seconds, count in timed.iter_rows()},
            )
        )
    return sorted(found, key=lambda item: -item.rulings)


def _ruling(row: dict[str, object]) -> Ruling:
    def _int(name: str) -> int | None:
        value = row.get(name)
        return int(value) if isinstance(value, int) else None

    def _str(name: str) -> str | None:
        value = row.get(name)
        return str(value) if value is not None else None

    issued = row.get("issued")
    return Ruling(
        document=int(row["document"]),  # pyright: ignore[reportArgumentType]
        entry=int(row["entry"]),  # pyright: ignore[reportArgumentType]
        kind=str(row.get("kind") or ""),
        issued=issued if isinstance(issued, datetime) else None,
        session=_str("session"),
        car=_int("car"),
        driver=_str("driver"),
        driver_code=_str("driver_code"),
        competitor=_str("competitor"),
        fact=_str("fact"),
        outcome=_str("outcome"),
        rule=_str("code"),
        offence=_str("offence"),
        sanction=Sanction(
            kind=str(row.get("sanction_kind") or "none"),
            outcome=str(row.get("bucket") or "none"),
            seconds=_int("seconds"),
            positions=_int("positions"),
            text=str(row.get("sanction_text") or ""),
        ),
    )


def stewards_view(
    incidents: pl.DataFrame | None,
    sanctions: pl.DataFrame | None,
    articles: pl.DataFrame | None,
    results: pl.DataFrame,
    context: EventContext,
    run_id: str,
    from_season: int = 2024,
    generated_at: datetime | None = None,
    race_control: pl.DataFrame | None = None,
    races: pl.DataFrame | None = None,
) -> StewardsView:
    if incidents is None or not incidents.height:
        frame = pl.DataFrame()
    else:
        frame = rulings_frame(incidents, sanctions, articles, results)
    here = (
        frame.filter((pl.col("season") == context.season) & (pl.col("round") == context.round))
        .sort("issued")
        .to_dicts()
        if frame.height
        else []
    )
    since = frame.filter(pl.col("season") >= from_season) if frame.height else frame
    turn_seasons, turns = turn_history(race_control, races, context.circuit_id)
    return StewardsView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        event=context,
        rulings=[_ruling(row) for row in here],
        from_season=from_season if since.height else None,
        total_rulings=since.height,
        precedents=precedents(since) if since.height else [],
        turn_seasons=turn_seasons,
        turns=turns,
    )
