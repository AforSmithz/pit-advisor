"""Counts every published F1 event document per season, so the ingest scope is a measured
number instead of an extrapolation from one busy season finale. Writes one json line per
event and skips events already recorded, so an interrupted run resumes."""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.fia.com"
CHAMPIONSHIP = "/documents/championships/fia-formula-one-world-championship-14"
SEASONS = {
    2021: "season-2021-1108",
    2022: "season-2022-2005",
    2023: "season-2023-2042",
    2024: "season-2024-2043",
    2025: "season-2025-2071",
}
UA = "pit-advisor/0.1 (portfolio project; abiseno.pramodana@gmail.com)"
# fia.com publishes Crawl-delay: 10; this is our own 120/hour cap, which is stricter
PAUSE = 30.0
ATTEMPTS = 5

EVENT_OPTION = re.compile(r'<option value="([^"]*/event/[^"]*)"')
DOC_LINK = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*\.pdf)"[^>]*>(?P<label>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
TAGS = re.compile(r"<[^>]+>")


def get(path: str) -> str:
    url = path if path.startswith("http") else BASE + path
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == ATTEMPTS - 1:
                raise
            # fia.com 504s on the busier event pages, and backing off harder clears it
            wait = PAUSE * (attempt + 1)
            print(f"  retry {attempt + 1} in {wait:.0f}s after {exc}", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise AssertionError


def clean(label: str) -> str:
    return " ".join(TAGS.sub(" ", label).split())


def documents(html: str) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for match in DOC_LINK.finditer(html):
        href = urllib.parse.unquote(match.group("href"))
        seen.setdefault(href, clean(match.group("label")))
    return [{"href": href, "title": title} for href, title in seen.items()]


def main() -> None:
    out = Path(sys.argv[1])
    failed: list[tuple[int, str]] = []
    done = set()
    if out.exists():
        done = {
            (record["season"], record["event"])
            for record in map(json.loads, out.read_text().splitlines())
        }

    with out.open("a") as sink:
        for season, slug in SEASONS.items():
            html = get(f"{CHAMPIONSHIP}/season/{slug}")
            events = sorted({urllib.parse.unquote(p) for p in EVENT_OPTION.findall(html)})
            print(f"{season}: {len(events)} events", flush=True)
            time.sleep(PAUSE)

            for path in events:
                name = path.rsplit("/event/", 1)[1]
                if (season, name) in done:
                    continue
                try:
                    docs = documents(get(urllib.parse.quote(path, safe="/:")))
                except (urllib.error.URLError, TimeoutError) as exc:
                    # nothing written, so a later resume picks this event up again
                    failed.append((season, name))
                    print(f"  {name}: FAILED {exc}", flush=True)
                    time.sleep(PAUSE)
                    continue
                sink.write(json.dumps({"season": season, "event": name, "documents": docs}) + "\n")
                sink.flush()
                print(f"  {name}: {len(docs)}", flush=True)
                time.sleep(PAUSE)

    if failed:
        print(f"\n{len(failed)} events failed, rerun to fill them:", flush=True)
        for season, name in failed:
            print(f"  {season} {name}", flush=True)


if __name__ == "__main__":
    main()
