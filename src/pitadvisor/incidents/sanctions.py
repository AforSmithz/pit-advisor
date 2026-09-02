import re
from enum import StrEnum
from typing import Final

from pydantic import BaseModel

from .parse import TYPOGRAPHIC


class Kind(StrEnum):
    NONE = "none"
    LAP_TIMES_DELETED = "lap_times_deleted"
    TIME_PENALTY = "time_penalty"
    PENALTY_POINTS = "penalty_points"
    GRID_DROP = "grid_drop"
    BACK_OF_GRID = "back_of_grid"
    PIT_LANE_START = "pit_lane_start"
    DRIVE_THROUGH = "drive_through"
    STOP_AND_GO = "stop_and_go"
    REPRIMAND = "reprimand"
    WARNING = "warning"
    FINE = "fine"
    DISQUALIFIED = "disqualified"
    SUSPENDED = "suspended"


class Sanction(BaseModel, frozen=True):
    kind: Kind
    seconds: int | None = None
    positions: int | None = None
    points: int | None = None
    # the stewards restate the running twelve month total in the same sentence
    points_total: int | None = None
    amount: int | None = None
    currency: str | None = None
    text: str


# order matters only in that the more specific pattern has to be tried first: a stop and go is
# also "a penalty of N seconds", and being sent to the pit lane is not a grid drop
PATTERNS: Final[tuple[tuple[Kind, re.Pattern[str]], ...]] = (
    (Kind.NONE, re.compile(r"\bno (?:further action|penalty|action)\b", re.I)),
    (Kind.LAP_TIMES_DELETED, re.compile(r"\bdeletion of the lap times?\b", re.I)),
    (
        Kind.STOP_AND_GO,
        re.compile(r"\b(?P<seconds>\d+)\s*seconds?\s*stop(?:\s*(?:and|&|/)\s*|-)?\s*go\b", re.I),
    ),
    (Kind.DRIVE_THROUGH, re.compile(r"\bdrive[- ]through penalty\b", re.I)),
    (
        Kind.TIME_PENALTY,
        re.compile(r"\b(?P<seconds>\d+)\s*second(?:s)? time penalty\b", re.I),
    ),
    (
        Kind.PENALTY_POINTS,
        re.compile(
            r"\b(?P<points>\d+)\s*penalty points?\b(?:[^.]*?total of (?P<total>\d+))?[^.]*", re.I
        ),
    ),
    (
        Kind.GRID_DROP,
        re.compile(r"\b(?:drop of )?(?P<positions>\d+)\s*grid positions?(?: penalty)?\b", re.I),
    ),
    (
        Kind.BACK_OF_GRID,
        re.compile(r"\bfrom the (?:back|rear) of the (?:starting )?grid\b", re.I),
    ),
    (Kind.PIT_LANE_START, re.compile(r"\bfrom the pit ?lane\b", re.I)),
    (Kind.DISQUALIFIED, re.compile(r"\bdisqualif(?:ied|ication)\b", re.I)),
    (Kind.REPRIMAND, re.compile(r"\breprimand\b(?:\s*\((?:non-)?driving\))?", re.I)),
    (Kind.WARNING, re.compile(r"\bwarning\b", re.I)),
    (
        Kind.FINE,
        re.compile(
            r"\b(?:fined|fine of)\s*(?P<currency>[€$£]|EUR|USD)?\s*(?P<amount>[\d,]+)"
            r"\s*(?P<spelled>euros?|dollars?|pounds?)?\b",
            re.I,
        ),
    ),
    (Kind.SUSPENDED, re.compile(r"\bsuspend(?:ed|ed sentence)\b", re.I)),
)

CURRENCIES: Final = {"€": "EUR", "$": "USD", "£": "GBP"}
SPELLED_CURRENCIES: Final = {"euro": "EUR", "dollar": "USD", "pound": "GBP"}

# a handful of decisions write the penalty out in words, "Ten Second Stop and Go Penalty"
WORDS: Final = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "fifteen": "15",
    "twenty": "20",
    "thirty": "30",
}
SPELLED = re.compile(r"\b(" + "|".join(WORDS) + r")\b(?=\s+(?:second|grid|penalty))", re.I)


def _number(found: re.Match[str], name: str) -> int | None:
    if name not in found.groupdict():
        return None
    value = found.group(name)
    return int(value.replace(",", "")) if value else None


def _currency(found: re.Match[str]) -> str | None:
    if "currency" not in found.groupdict():
        return None
    symbol = found.group("currency")
    if symbol is not None:
        return CURRENCIES.get(symbol, symbol.upper())
    spelled = found.groupdict().get("spelled")
    return SPELLED_CURRENCIES.get(spelled.lower().rstrip("s")) if spelled else None


def sanctions(outcome: str | None) -> list[Sanction]:
    """Every sanction the stewards imposed, in the order the decision states them. A decision
    carries a list: a driver penalty, its penalty points and a fine on the team can all be one
    sentence."""
    if not outcome:
        return []
    folded = "".join(TYPOGRAPHIC.get(character, character) for character in outcome)
    # the spelled number is replaced in place, so an offset into the folded text still points at
    # the same character of the original and the stored quote stays a substring of it
    text = SPELLED.sub(lambda word: WORDS[word.group(1).lower()].ljust(len(word.group(1))), folded)
    claimed: list[tuple[int, int]] = []
    found: list[tuple[int, Sanction]] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            start = match.start()
            if any(low <= start < high for low, high in claimed):
                continue
            claimed.append((start, match.end()))
            found.append(
                (
                    start,
                    Sanction(
                        kind=kind,
                        seconds=_number(match, "seconds"),
                        positions=_number(match, "positions"),
                        points=_number(match, "points"),
                        points_total=_number(match, "total"),
                        amount=_number(match, "amount"),
                        currency=_currency(match),
                        text=outcome[match.start() : match.end()].strip(),
                    ),
                )
            )
    return [sanction for _, sanction in sorted(found, key=lambda pair: pair[0])]
