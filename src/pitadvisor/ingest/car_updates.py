import re
from datetime import datetime
from typing import Any, Final

from pitadvisor.ingest.raw_store import ObjectStore, write_bronze_by_event, write_quarantine
from pitadvisor.quality import contracts
from pitadvisor.types import EventKey

KIND: Final = "car-presentation-submissions"
# longest names first, so "racing bulls" is not read as "red bull racing" and "kick sauber" is
# matched before a bare "sauber"
TEAMS: Final = (
    ("red bull racing", "red_bull"),
    ("racing bulls", "rb"),
    ("visa cash app rb", "rb"),
    ("rb formula one", "rb"),
    ("kick sauber", "sauber"),
    ("sauber", "sauber"),
    ("audi", "audi"),
    ("cadillac", "cadillac"),
    ("mercedes", "mercedes"),
    ("ferrari", "ferrari"),
    ("mclaren", "mclaren"),
    ("aston martin", "aston_martin"),
    ("alpine", "alpine"),
    ("williams", "williams"),
    ("haas", "haas"),
)
NO_UPDATES: Final = re.compile(r"No updates submitted", re.I)
ROW: Final = re.compile(r"^\s*(\d{1,2})\s+\S", re.M)
REASON: Final = re.compile(r"Performance\s*-|Circuit\s+specific\s*-|Reliability\b")
# the header separates the date's words with non-breaking spaces, which \s covers and " " does not
ISSUED: Final = re.compile(r"Date\s+(\d{1,2}\s\w+\s\d{4})\s*\nTime\s+(\d{1,2}:\d{2})")


def team_of(line: str) -> str | None:
    """A team header is a line written in capitals that names one of the teams."""
    stripped = line.strip()
    letters = [char for char in stripped if char.isalpha()]
    if len(letters) < 4 or any(char.islower() for char in letters):
        return None
    lowered = stripped.lower()
    return next((team for name, team in TEAMS if name in lowered), None)


def _category(reason: str) -> str:
    plain = " ".join(reason.split()).lower()
    if plain.startswith("performance"):
        return "performance"
    if plain.startswith("circuit"):
        return "circuit"
    return "reliability"


def _issued(text: str) -> datetime | None:
    found = ISSUED.search(text)
    if found is None:
        return None
    try:
        day = " ".join(found.group(1).split())
        return datetime.strptime(f"{day} {found.group(2)}", "%d %B %Y %H:%M")
    except ValueError:
        return None


def parse(text: str) -> list[dict[str, Any]]:
    """One row per declared update per team, plus one row for a team that declared none."""
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str, str]] = []
    offset = 0
    for line in lines:
        team = team_of(line)
        if team is not None:
            starts.append((offset, team, line.strip()))
        offset += len(line)
    rows: list[dict[str, Any]] = []
    for index, (begin, team, header) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        section = text[begin:end]
        declared = NO_UPDATES.search(section)
        if declared is not None:
            rows.append(
                {
                    "constructor_id": team,
                    "team": header,
                    "ordinal": 0,
                    "category": "none",
                    "reason": declared.group(0),
                    "text": declared.group(0),
                }
            )
            continue
        numbered = list(ROW.finditer(section))
        expected = 1
        kept: list[re.Match[str]] = []
        for match in numbered:
            if int(match.group(1)) == expected:
                kept.append(match)
                expected += 1
        for position, match in enumerate(kept):
            stop = kept[position + 1].start() if position + 1 < len(kept) else len(section)
            chunk = section[match.start() : stop].strip()
            reason = REASON.search(chunk)
            rows.append(
                {
                    "constructor_id": team,
                    "team": header,
                    "ordinal": position + 1,
                    "category": _category(reason.group(0)) if reason else "unknown",
                    "reason": reason.group(0) if reason else "",
                    "text": chunk,
                }
            )
    return rows


def materialize(
    store: ObjectStore,
    key: EventKey,
    raw_key: str,
    document_name: str,
    text: str,
    stamp: dict[str, Any],
) -> tuple[int, int]:
    issued = _issued(text)
    records = [
        {
            **stamp,
            "season": key.season,
            "round": key.round,
            "document_name": document_name,
            "issued": issued,
            "raw_key": raw_key,
            **row,
        }
        for row in parse(text)
    ]
    kept, dropped = contracts.validate("car_updates", contracts.CarUpdateRow, records)
    write_quarantine(store, "car_updates", key, str(stamp["run_id"]), dropped)
    if kept:
        write_bronze_by_event(store, "car_updates", kept)
    return len(kept), len(dropped)
