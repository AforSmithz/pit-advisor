import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote, unquote

from pydantic import BaseModel

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import META_SUFFIX, ObjectStore, RawStore
from pitadvisor.types import EventKey, Layer, Provenance, Source

WIKI_API: Final = "https://en.wikipedia.org/w/api.php"
WIKI_PREFIX: Final = "https://en.wikipedia.org/wiki/"
METADATA_SUFFIX: Final = ".metadata.json"

# every one of these is a table or a link farm in the source, and what survives the plain-text
# extract is a caption with numbers in it that the marts already answer better
DROPPED_SECTIONS: Final = (
    "references",
    "external links",
    "see also",
    "notes",
    "further reading",
    "bibliography",
    "classification",
    "standings",
    "results",
    "entrants",
)
MIN_CHARACTERS: Final = 400


class Page(BaseModel, frozen=True):
    title: str
    url: str
    kind: str
    season: int
    round: int

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")


class DocOutcome(BaseModel, frozen=True):
    title: str
    kind: str
    key: str | None = None
    characters: int = 0
    skipped: str | None = None


def linked_pages(store: ObjectStore) -> list[Page]:
    """The wikipedia links jolpica already handed us, read back out of raw.

    Only the races payloads: every jolpica response carries the same RaceTable, but a season of
    laps is hundreds of paginated objects and reading them all to reach a url that is also in a
    two kilobyte file is a lot of GETs for nothing."""
    seen: dict[str, Page] = {}
    for item in sorted(store.list(f"{Layer.RAW}/source={Source.JOLPICA}/"), key=lambda o: o.key):
        if item.key.endswith(META_SUFFIX) or "/races-" not in item.key:
            continue
        payload = json.loads(store.get(item.key))
        table = payload.get("MRData", {}).get("RaceTable", {})
        for race in table.get("Races", []):
            for url, kind in (
                (race.get("url"), "race"),
                (race.get("Circuit", {}).get("url"), "circuit"),
            ):
                page = _page(url, kind, int(race["season"]), int(race["round"]))
                if page is not None:
                    seen.setdefault(page.title, page)
    return sorted(seen.values(), key=lambda page: (page.kind, page.title))


def _page(url: str | None, kind: str, season: int, round_: int) -> Page | None:
    if not url or not url.startswith(WIKI_PREFIX):
        return None
    title = unquote(url[len(WIKI_PREFIX) :]).replace("_", " ")
    return Page(title=title, url=url, kind=kind, season=season, round=round_)


def api_url(title: str) -> str:
    # explaintext is what drops the infobox and every results table, which is the point
    query = (
        "action=query&format=json&formatversion=2&prop=extracts"
        f"&explaintext=1&redirects=1&titles={quote(title)}"
    )
    return f"{WIKI_API}?{query}"


def extract_of(payload: dict[str, Any]) -> str:
    pages = payload.get("query", {}).get("pages", [])
    if not pages:
        return ""
    return str(pages[0].get("extract", ""))


def prose(text: str) -> str:
    kept: list[str] = []
    dropping = False
    for block in text.split("\n"):
        heading = re.fullmatch(r"=+ (.+?) =+", block.strip())
        if heading is not None:
            name = heading.group(1).strip().lower()
            dropping = any(word in name for word in DROPPED_SECTIONS)
            if not dropping:
                kept.append(f"\n{heading.group(1).strip()}\n")
            continue
        if not dropping and block.strip():
            kept.append(block.strip())
    return "\n".join(kept).strip()


def doc_key(page: Page) -> str:
    return f"{Layer.DOCS}/source={Source.WIKIPEDIA}/kind={page.kind}/{page.slug}.txt"


def metadata_of(page: Page) -> dict[str, Any]:
    return {
        "metadataAttributes": {
            "source": str(Source.WIKIPEDIA),
            "kind": page.kind,
            "season": page.season,
            "title": page.title,
            "url": page.url,
            # CC BY-SA, so the licence travels with the passage the agent ends up quoting
            "licence": "CC BY-SA 4.0",
        }
    }


def write_doc(store: ObjectStore, page: Page, text: str) -> str:
    key = doc_key(page)
    store.put(key, text.encode())
    store.put(key + METADATA_SUFFIX, json.dumps(metadata_of(page), indent=2).encode())
    return key


def ingest_page(
    store: ObjectStore,
    raw: RawStore,
    page: Page,
    ledger: Ledger,
    run_id: str,
    limiter: RateLimiter | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
) -> DocOutcome:
    url = api_url(page.title)
    response = fetch(url, ledger, limiter)
    if response.not_modified:
        return DocOutcome(title=page.title, kind=page.kind, skipped="not modified")
    provenance = Provenance(
        run_id=run_id,
        source=Source.WIKIPEDIA,
        url=url,
        fetched_at=response.fetched_at,
        status=response.status,
        etag=response.etag,
    )
    raw.land(EventKey(season=page.season, round=page.round), page.slug, response.body, provenance)
    text = prose(extract_of(json.loads(response.body)))
    if len(text) < MIN_CHARACTERS:
        return DocOutcome(title=page.title, kind=page.kind, skipped="too short to be an article")
    return DocOutcome(
        title=page.title,
        kind=page.kind,
        key=write_doc(store, page, f"{page.title}\n\n{text}"),
        characters=len(text),
    )


def ingest_wikipedia(
    store: ObjectStore,
    raw: RawStore,
    ledger: Ledger,
    run_id: str,
    limiter: RateLimiter | None = None,
    pages: list[Page] | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
    limit: int | None = None,
) -> list[DocOutcome]:
    wanted = pages if pages is not None else linked_pages(store)
    if limit is not None:
        wanted = wanted[:limit]
    return [ingest_page(store, raw, page, ledger, run_id, limiter, fetch) for page in wanted]


class Curated(BaseModel, frozen=True):
    title: str
    kind: str
    source: Source = Source.FIA_DOCS
    season: int | None = None


def curated_key(item: Curated, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", item.title.lower()).strip("-")
    return f"{Layer.DOCS}/source={item.source}/kind={item.kind}/{slug}{suffix}"


def add_curated(store: ObjectStore, path: Path, item: Curated) -> str:
    """Regulations and methodology notes are dropped in by hand, which §3 of the plan allows."""
    key = curated_key(item, path.suffix or ".txt")
    store.put(key, path.read_bytes())
    attributes: dict[str, Any] = {
        "source": str(item.source),
        "kind": item.kind,
        "title": item.title,
    }
    if item.season is not None:
        attributes["season"] = item.season
    store.put(
        key + METADATA_SUFFIX,
        json.dumps({"metadataAttributes": attributes}, indent=2).encode(),
    )
    return key


def corpus(store: ObjectStore) -> list[str]:
    return sorted(
        item.key for item in store.list(f"{Layer.DOCS}/") if not item.key.endswith(METADATA_SUFFIX)
    )
