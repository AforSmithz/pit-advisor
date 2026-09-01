import re
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Final
from urllib.parse import quote, unquote

from pydantic import BaseModel

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import RawOverwriteError, RawStore
from pitadvisor.types import EventKey, Provenance, Source

BASE: Final = "https://www.fia.com"
CHAMPIONSHIP: Final = "/documents/championships/fia-formula-one-world-championship-14"
FIA_PAUSE_SECONDS: Final = 10.0

# the stewards renamed the charge document between 2022 and 2023 and kept everything else, so a
# filter that knows only one of the two words silently loses two whole seasons
WANTED_KINDS: Final = frozenset(
    {
        "decision",
        "offence",
        "infringement",
        "final race classification",
        "final qualifying classification",
        "final starting grid",
    }
)

# fia.com and jolpica disagree about three race names, and 2023 shouts one of them
EVENT_ALIASES: Final = {
    "brazilian grand prix": "sao paulo grand prix",
    "mexican grand prix": "mexico city grand prix",
    "saudi arabia grand prix": "saudi arabian grand prix",
}
# a right of review is heard weeks after the race it reopens and files under its own event page
REVIEW_SUFFIX: Final = " - right of review"

SEASON_OPTION = re.compile(
    rf'<option value="({re.escape(CHAMPIONSHIP)}/season/season-(\d{{4}})-\d+)"'
)
EVENT_OPTION = re.compile(r'<option value="([^"]*/event/[^"]*)"')
DOC_LINK = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*\.pdf)"[^>]*>(?P<label>.*?)</a>', re.IGNORECASE | re.DOTALL
)
TAGS = re.compile(r"<[^>]+>")
PUBLISHED = re.compile(r"\s+Published on (\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}).*$", re.I)
DOCNUM = re.compile(r"^Doc\s+\d+\s*-\s*", re.I)
CAR = re.compile(r"\bcar\s+(\d+)", re.I)
LANDED = re.compile(r"-\d{8}T\d{9}Z\.pdf$")


class Listing(BaseModel, frozen=True):
    season: int
    event: str
    title: str
    kind: str
    href: str
    published: datetime | None = None
    car: int | None = None

    @property
    def url(self) -> str:
        # filenames carry spaces, per cent signs from the 107% rule, and "São Paulo". the first
        # two are malformed escapes and the third is not ascii at all, which urllib refuses
        # outright. everything else stays literal, because fia.com serves apostrophes unescaped
        return BASE + quote(self.href, safe="/:'()[]!$&*+,;=@~._-")


def fold(name: str) -> str:
    stripped = "".join(
        char for char in unicodedata.normalize("NFKD", name) if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", stripped).strip().lower()


def canonical(event: str) -> str:
    name = fold(event)
    name = name.removesuffix(REVIEW_SUFFIX)
    return EVENT_ALIASES.get(name, name)


def round_of(event: str, rounds: dict[str, int]) -> int | None:
    return {fold(name): number for name, number in rounds.items()}.get(canonical(event))


def _clean(label: str) -> str:
    return " ".join(TAGS.sub(" ", label).split())


def _published(title: str) -> datetime | None:
    found = PUBLISHED.search(title)
    if found is None:
        return None
    return datetime.strptime(found.group(1), "%d.%m.%y %H:%M")


def listings(html: str, season: int, event: str) -> list[Listing]:
    seen: dict[str, Listing] = {}
    for match in DOC_LINK.finditer(html):
        href = unquote(match.group("href"))
        if href in seen:
            continue
        label = _clean(match.group("label"))
        title = DOCNUM.sub("", PUBLISHED.sub("", label)).strip()
        car = CAR.search(title)
        seen[href] = Listing(
            season=season,
            event=event,
            title=title,
            kind=title.split(" - ")[0].strip().lower(),
            href=href,
            published=_published(label),
            car=int(car.group(1)) if car else None,
        )
    return list(seen.values())


def wanted(listing: Listing) -> bool:
    return listing.kind in WANTED_KINDS


def seasons(html: str) -> dict[int, str]:
    return {int(year): path for path, year in SEASON_OPTION.findall(html)}


def events(html: str) -> list[str]:
    return sorted({unquote(path).rsplit("/event/", 1)[1] for path in EVENT_OPTION.findall(html)})


class DocumentOutcome(BaseModel, frozen=True):
    title: str
    key: str | None = None
    skipped: str | None = None


def ingest_document(
    raw: RawStore,
    listing: Listing,
    round_: int,
    ledger: Ledger,
    run_id: str,
    limiter: RateLimiter | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
) -> DocumentOutcome:
    response = fetch(listing.url, ledger, limiter, timeout=90.0, accept="application/pdf")
    if response.not_modified:
        return DocumentOutcome(title=listing.title, skipped="not modified")
    provenance = Provenance(
        run_id=run_id,
        source=Source.FIA_DOCS,
        url=listing.url,
        fetched_at=response.fetched_at,
        status=response.status,
        etag=response.etag,
    )
    key = raw.land(
        EventKey(season=listing.season, round=round_),
        _name(listing),
        response.body,
        provenance,
        suffix="pdf",
    )
    return DocumentOutcome(title=listing.title, key=key)


def _name(listing: Listing) -> str:
    # the published stamp is part of the name because a decision is reissued as "(corrected)"
    # under a title that differs from the original by two words
    stamp = listing.published.strftime("%Y%m%dT%H%M") if listing.published else "unstamped"
    slug = re.sub(r"[^a-z0-9]+", "-", fold(listing.title)).strip("-")[:80]
    return f"{stamp}-{slug}"


def _landed(raw: RawStore, season: int, round_: int, name: str) -> bool:
    # versions() matches on a prefix, so a document whose name is a prefix of another one is
    # reported as already present and never fetched. "... (corrected)" is exactly that shape
    key = EventKey(season=season, round=round_)
    return any(
        LANDED.sub("", found.rsplit("/", 1)[1]) == name
        for found in raw.versions(Source.FIA_DOCS, key, name)
    )


def walk(
    raw: RawStore,
    ledger: Ledger,
    run_id: str,
    rounds: dict[int, dict[str, int]],
    limiter: RateLimiter | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
    wanted_seasons: list[int] | None = None,
    pause_seconds: float = FIA_PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    refresh: bool = False,
    on_document: Callable[[DocumentOutcome], None] | None = None,
) -> list[DocumentOutcome]:
    last = [clock() - pause_seconds]

    def pace() -> None:
        # crawl-delay is the interval between requests, so the fetch belongs inside it. sleeping
        # the delay and then fetching makes every request cost the delay plus the round trip
        waited = clock() - last[0]
        if waited < pause_seconds:
            sleep(pause_seconds - waited)
        last[0] = clock()

    def page(path: str) -> str:
        pace()
        # an event name is a path segment with spaces and accents in it
        url = BASE + quote(path, safe="/:")
        return fetch(url, ledger, limiter, timeout=90.0, accept="text/html").body.decode(
            "utf-8", "replace"
        )

    outcomes: list[DocumentOutcome] = []

    def record(outcome: DocumentOutcome) -> None:
        outcomes.append(outcome)
        if on_document is not None:
            on_document(outcome)

    for season, path in sorted(seasons(page(CHAMPIONSHIP)).items()):
        if wanted_seasons is not None and season not in wanted_seasons:
            continue
        for event in events(page(path)):
            round_ = round_of(event, rounds.get(season, {}))
            if round_ is None:
                record(DocumentOutcome(title=event, skipped="not a championship round"))
                continue
            try:
                html = page(f"{path}/event/{event}")
            except (http.FetchError, OSError) as exc:
                # fia.com 504s under load often enough that one bad listing must not end a season
                record(DocumentOutcome(title=event, skipped=f"listing failed: {exc}"))
                continue
            for listing in listings(html, season, event):
                if not wanted(listing):
                    continue
                if not refresh and _landed(raw, season, round_, _name(listing)):
                    continue
                pace()
                try:
                    outcome = ingest_document(raw, listing, round_, ledger, run_id, limiter, fetch)
                except (http.FetchError, OSError, ValueError, RawOverwriteError) as exc:
                    # a name that cannot be turned into a url must not end the season
                    outcome = DocumentOutcome(title=listing.title, skipped=f"{exc}")
                record(outcome)
    return outcomes
