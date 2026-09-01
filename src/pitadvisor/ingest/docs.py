import io
import json
import re
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote, unquote

import yaml
from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import META_SUFFIX, ObjectStore, RawStore
from pitadvisor.types import EventKey, Layer, Provenance, SeasonKey, Source

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
# wikipedia answers 429 to a burst from an anonymous client however much hourly budget the
# bucket thinks is left, so the walk spaces itself out on top of the token bucket
PAUSE_SECONDS: Final = 1.0
# three refusals in a row is the site telling us to come back later, not one bad page
CONSECUTIVE_FAILURES: Final = 3
REGULATIONS: Final = Path("data/reference/regulations.yml")
METHODOLOGY: Final = Path("data/reference/methodology")
# a note we wrote about our own metrics is not an FIA document, and filing it under fia_docs
# would attribute it to them and hand it back under a source filter meant for the regulations
KIND_SOURCES: Final = {"regulation": Source.FIA_DOCS, "decision": Source.FIA_DOCS}
# fia.com publishes Crawl-delay: 10 in robots.txt and the whole set is ten documents, so there
# is no reason not to honour it
FIA_PAUSE_SECONDS: Final = 10.0


class Page(BaseModel, frozen=True):
    title: str
    url: str
    kind: str
    season: int
    round: int

    @property
    def slug(self) -> str:
        return slug_of(self.title)


def slug_of(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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
    refresh: bool = False,
    pause_seconds: float = PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_page: Callable[[DocOutcome], None] | None = None,
) -> list[DocOutcome]:
    wanted = pages if pages is not None else linked_pages(store)
    if not refresh:
        wanted = [page for page in wanted if not store.exists(doc_key(page))]
    if limit is not None:
        wanted = wanted[:limit]
    outcomes: list[DocOutcome] = []
    failures = 0
    for index, page in enumerate(wanted):
        if index and pause_seconds:
            sleep(pause_seconds)
        try:
            outcome = ingest_page(store, raw, page, ledger, run_id, limiter, fetch)
            failures = 0
        except http.FetchError as exc:
            failures += 1
            outcome = DocOutcome(title=page.title, kind=page.kind, skipped=f"{exc}")
        outcomes.append(outcome)
        if on_page is not None:
            on_page(outcome)
        if failures >= CONSECUTIVE_FAILURES:
            outcomes.append(
                DocOutcome(
                    title="-",
                    kind="-",
                    skipped=f"stopped after {failures} refusals in a row, run it again later",
                )
            )
            break
    return outcomes


class Curated(BaseModel, frozen=True):
    title: str
    kind: str
    source: Source = Source.CURATED
    season: int | None = None
    issued: date | None = None
    # what to key on when the title is not the right name: a note's title is a sentence, and
    # rewording it would orphan the object it wrote last time
    slug: str | None = None


def source_of(kind: str) -> Source:
    return KIND_SOURCES.get(kind, Source.CURATED)


def curated_key(item: Curated, suffix: str) -> str:
    # the regulations are reissued mid-season, so the issue date is part of the name or the
    # august version silently overwrites the march one
    name = item.slug or (
        item.title if item.issued is None else f"{item.title} {item.issued.isoformat()}"
    )
    return f"{Layer.DOCS}/source={item.source}/kind={item.kind}/{slug_of(name)}{suffix}"


def add_curated(store: ObjectStore, path: Path, item: Curated) -> str:
    """Methodology notes and anything the manifest does not cover are dropped in by hand, which
    §3 of the plan allows."""
    body = path.read_bytes()
    # the corpus is text whichever door a document comes in by. a pdf sitting in docs/ is one
    # the local retriever cannot read at all
    if path.suffix.lower() == ".pdf":
        return write_curated(store, item, f"{item.title}\n\n{pdf_text(body)}".encode(), ".txt")
    return write_curated(store, item, body, path.suffix or ".txt")


def write_curated(store: ObjectStore, item: Curated, body: bytes, suffix: str) -> str:
    key = curated_key(item, suffix)
    store.put(key, body)
    attributes: dict[str, Any] = {
        "source": str(item.source),
        "kind": item.kind,
        "title": item.title,
    }
    if item.season is not None:
        attributes["season"] = item.season
    if item.issued is not None:
        attributes["issued"] = item.issued.isoformat()
    store.put(
        key + METADATA_SUFFIX,
        json.dumps({"metadataAttributes": attributes}, indent=2).encode(),
    )
    return key


def methodology_note(path: Path) -> tuple[Curated, str]:
    """The first line of the file is the title, which keeps the note and its name together in
    one place a human edits."""
    text = path.read_text().strip()
    item = Curated(title=text.partition("\n")[0].strip(), kind="methodology", slug=path.stem)
    return item, text


def ingest_methodology(
    store: ObjectStore,
    directory: Path = METHODOLOGY,
    refresh: bool = False,
    on_page: Callable[[DocOutcome], None] | None = None,
) -> list[DocOutcome]:
    """Our own notes, which are in the repo rather than upstream, so there is nothing to fetch
    and nothing to land in raw: git is their provenance."""
    outcomes: list[DocOutcome] = []
    for path in sorted(directory.glob("*.txt")):
        item, text = methodology_note(path)
        key, body = curated_key(item, ".txt"), text.encode()
        if not refresh and store.exists(key) and store.get(key) == body:
            outcome = DocOutcome(title=item.title, kind="methodology", skipped="unchanged")
        else:
            write_curated(store, item, body, ".txt")
            outcome = DocOutcome(
                title=item.title, kind="methodology", key=key, characters=len(text)
            )
        outcomes.append(outcome)
        if on_page is not None:
            on_page(outcome)
    return outcomes


class Regulation(BaseModel, frozen=True):
    season: int
    title: str
    issued: date
    url: str

    @property
    def curated(self) -> Curated:
        return Curated(
            title=self.title,
            kind="regulation",
            source=Source.FIA_DOCS,
            season=self.season,
            issued=self.issued,
        )


def regulations(path: Path = REGULATIONS) -> list[Regulation]:
    return [Regulation.model_validate(row) for row in yaml.safe_load(path.read_text())]


def pdf_text(body: bytes) -> str:
    pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(pages)).strip()


def ingest_regulation(
    store: ObjectStore,
    raw: RawStore,
    item: Regulation,
    ledger: Ledger,
    run_id: str,
    limiter: RateLimiter | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
) -> DocOutcome:
    # the regulations are ten megabytes of pdf, not a json payload the default header asks for
    response = fetch(item.url, ledger, limiter, timeout=60.0, accept="application/pdf")
    if response.not_modified:
        return DocOutcome(title=item.title, kind="regulation", skipped="not modified")
    provenance = Provenance(
        run_id=run_id,
        source=Source.FIA_DOCS,
        url=item.url,
        fetched_at=response.fetched_at,
        status=response.status,
        etag=response.etag,
    )
    # the pdf is the source payload and lands verbatim, the corpus gets the text out of it
    raw.land(
        SeasonKey(season=item.season), slug_of(item.title), response.body, provenance, suffix="pdf"
    )
    text = pdf_text(response.body)
    if len(text) < MIN_CHARACTERS:
        return DocOutcome(title=item.title, kind="regulation", skipped="no text layer to extract")
    key = write_curated(store, item.curated, f"{item.title}\n\n{text}".encode(), ".txt")
    return DocOutcome(title=item.title, kind="regulation", key=key, characters=len(text))


def ingest_regulations(
    store: ObjectStore,
    raw: RawStore,
    ledger: Ledger,
    run_id: str,
    limiter: RateLimiter | None = None,
    wanted: list[Regulation] | None = None,
    fetch: Callable[..., http.Response] = http.fetch,
    refresh: bool = False,
    pause_seconds: float = FIA_PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_page: Callable[[DocOutcome], None] | None = None,
) -> list[DocOutcome]:
    items = wanted if wanted is not None else regulations()
    if not refresh:
        items = [item for item in items if not store.exists(curated_key(item.curated, ".txt"))]
    outcomes: list[DocOutcome] = []
    for index, item in enumerate(items):
        if index and pause_seconds:
            sleep(pause_seconds)
        try:
            outcome = ingest_regulation(store, raw, item, ledger, run_id, limiter, fetch)
        except (http.FetchError, OSError, PyPdfError) as exc:
            outcome = DocOutcome(title=item.title, kind="regulation", skipped=f"{exc}")
        outcomes.append(outcome)
        if on_page is not None:
            on_page(outcome)
    return outcomes


def corpus(store: ObjectStore) -> list[str]:
    return sorted(
        item.key for item in store.list(f"{Layer.DOCS}/") if not item.key.endswith(METADATA_SUFFIX)
    )
