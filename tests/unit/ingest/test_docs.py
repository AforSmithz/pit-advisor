import json
from datetime import UTC, date, datetime

import pytest

from pitadvisor.ingest import docs
from pitadvisor.ingest.docs import Curated, Page
from pitadvisor.ingest.http import FetchError as HttpFetchError
from pitadvisor.ingest.http import Response
from pitadvisor.types import Source

NOW = datetime(2026, 8, 31, tzinfo=UTC)

ARTICLE = """The 2024 Bahrain Grand Prix was a race held on 2 March 2024.

== Background ==
The championship began in Bahrain for the first time since 2021.

== Race ==
Verstappen led from pole and was never headed. The medium tyre proved the stronger race
compound and the field split on strategy behind him, with the second stint deciding almost
every position in the top ten. Track temperature fell through the evening, which flattered
the cars that had struggled for rear grip in the earlier sessions and left the rest managing
degradation from the first stint onwards.

== Classification ==
Pos No Driver Team

== References ==
Some citation.
"""


def raw_payload(url="https://en.wikipedia.org/wiki/2024_Bahrain_Grand_Prix"):
    return {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2024",
                        "round": "1",
                        "url": url,
                        "Circuit": {
                            "url": "https://en.wikipedia.org/wiki/Bahrain_International_Circuit"
                        },
                    }
                ]
            }
        }
    }


def land_raw(store, payload=None):
    store.put(
        "raw/source=jolpica/season=2024/round=01/races-20240101T000000000Z.json",
        json.dumps(payload or raw_payload()).encode(),
    )
    store.put(
        "raw/source=jolpica/season=2024/round=01/races-20240101T000000000Z.json.meta.json",
        b"{}",
    )


def page(kind="race"):
    return Page(
        title="2024 Bahrain Grand Prix",
        url="https://en.wikipedia.org/wiki/2024_Bahrain_Grand_Prix",
        kind=kind,
        season=2024,
        round=1,
    )


class FakeWiki:
    def __init__(self, extract=ARTICLE, status=200):
        self.extract = extract
        self.status = status
        self.calls = []

    def __call__(self, url, ledger, limiter=None, **_):
        self.calls.append(url)
        body = (
            b""
            if self.status == 304
            else json.dumps(
                {
                    "query": {
                        "pages": [{"title": "2024 Bahrain Grand Prix", "extract": self.extract}]
                    }
                }
            ).encode()
        )
        return Response(url=url, status=self.status, body=body, etag='"w1"', fetched_at=NOW)


def test_the_links_jolpica_already_gave_us_are_read_back_out_of_raw(store):
    land_raw(store)
    found = docs.linked_pages(store)
    assert {item.kind for item in found} == {"race", "circuit"}
    assert {item.title for item in found} == {
        "2024 Bahrain Grand Prix",
        "Bahrain International Circuit",
    }


def test_a_race_with_no_wikipedia_link_contributes_nothing(store):
    land_raw(store, raw_payload(url=None))
    assert [item.kind for item in docs.linked_pages(store)] == ["circuit"]


def test_a_link_that_is_not_wikipedia_is_ignored(store):
    land_raw(store, raw_payload(url="https://example.com/race"))
    assert all(item.url.startswith(docs.WIKI_PREFIX) for item in docs.linked_pages(store))


def test_the_same_page_linked_from_two_races_is_fetched_once(store):
    land_raw(store)
    store.put(
        "raw/source=jolpica/season=2024/round=02/races-20240101T000000000Z.json",
        json.dumps(raw_payload()).encode(),
    )
    assert len(docs.linked_pages(store)) == 2


def test_a_season_of_lap_pages_is_not_downloaded_to_find_a_url(store):
    land_raw(store)
    for offset in range(0, 500, 100):
        store.put(
            f"raw/source=jolpica/season=2024/round=01/laps-offset{offset:04d}-2024.json",
            b"not json, and never read",
        )
    assert len(docs.linked_pages(store)) == 2


def test_the_api_call_asks_for_plain_text_because_that_is_what_drops_the_tables():
    url = docs.api_url("2024 Bahrain Grand Prix")
    assert "explaintext=1" in url
    assert "2024%20Bahrain%20Grand%20Prix" in url


def test_results_and_reference_sections_do_not_reach_the_corpus():
    text = docs.prose(ARTICLE)
    assert "Verstappen led from pole" in text
    assert "Classification" not in text
    assert "Some citation" not in text


def test_the_sections_that_are_prose_keep_their_headings():
    assert "Background" in docs.prose(ARTICLE)


def test_a_fetched_page_lands_in_raw_and_writes_a_document(store, raw, ledger):
    outcome = docs.ingest_page(store, raw, page(), ledger, "run-1", fetch=FakeWiki())
    assert outcome.key == "docs/source=wikipedia/kind=race/2024-bahrain-grand-prix.txt"
    assert store.exists(outcome.key)
    assert list(store.list("raw/source=wikipedia/season=2024/round=01/"))


def test_the_document_carries_the_metadata_the_knowledge_base_filters_on(store, raw, ledger):
    outcome = docs.ingest_page(store, raw, page(), ledger, "run-1", fetch=FakeWiki())
    assert outcome.key is not None
    attributes = json.loads(store.get(outcome.key + ".metadata.json"))["metadataAttributes"]
    assert attributes["source"] == "wikipedia"
    assert attributes["season"] == 2024
    assert attributes["licence"] == "CC BY-SA 4.0"


def test_a_stub_article_is_skipped_rather_than_indexed(store, raw, ledger):
    outcome = docs.ingest_page(store, raw, page(), ledger, "run-1", fetch=FakeWiki("too short"))
    assert outcome.skipped == "too short to be an article"
    assert outcome.key is None


def test_a_304_costs_a_request_and_writes_nothing(store, raw, ledger):
    outcome = docs.ingest_page(store, raw, page(), ledger, "run-1", fetch=FakeWiki(status=304))
    assert outcome.skipped == "not modified"
    assert not list(store.list("docs/"))


def test_the_whole_corpus_walk_returns_one_outcome_per_page(store, raw, ledger):
    land_raw(store)
    outcomes = docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=FakeWiki())
    assert len(outcomes) == 2


def test_the_walk_can_be_capped_while_backfilling(store, raw, ledger):
    land_raw(store)
    assert len(docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=FakeWiki(), limit=1)) == 1


def test_a_hand_dropped_regulation_lands_with_its_metadata(store, tmp_path):
    source = tmp_path / "sporting.txt"
    source.write_text("Article 1. The championship.")
    key = docs.add_curated(
        store,
        source,
        Curated(
            title="Sporting Regulations 2025",
            kind="regulation",
            source=docs.source_of("regulation"),
            season=2025,
        ),
    )
    assert key == "docs/source=fia_docs/kind=regulation/sporting-regulations-2025.txt"
    assert json.loads(store.get(key + ".metadata.json"))["metadataAttributes"]["season"] == 2025


def test_a_reissued_regulation_does_not_overwrite_the_earlier_one(store, tmp_path):
    source = tmp_path / "sporting.pdf"
    source.write_bytes(one_page_pdf(["Article 1. The championship."]))
    keys = [
        docs.add_curated(
            store,
            source,
            Curated(
                title="Sporting Regulations",
                kind="regulation",
                source=docs.source_of("regulation"),
                season=2024,
                issued=issued,
            ),
        )
        for issued in (date(2024, 2, 28), date(2024, 8, 16))
    ]
    assert keys[0] != keys[1]
    assert keys[1].endswith("sporting-regulations-2024-08-16.txt")
    attributes = json.loads(store.get(keys[1] + ".metadata.json"))["metadataAttributes"]
    assert attributes["issued"] == "2024-08-16"
    assert attributes["season"] == 2024


def test_the_corpus_listing_leaves_the_sidecars_out(store, raw, ledger):
    land_raw(store)
    docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=FakeWiki())
    assert all(not key.endswith(".metadata.json") for key in docs.corpus(store))


def test_wikipedia_is_a_source_the_raw_layout_knows_about():
    assert Source.WIKIPEDIA in set(Source)


@pytest.mark.parametrize("kind", ["race", "circuit"])
def test_the_key_says_which_kind_of_page_it_is(kind):
    assert f"kind={kind}" in docs.doc_key(page(kind))


class Refuses:
    def __init__(self, fail_after=0):
        self.fail_after = fail_after
        self.calls = 0

    def __call__(self, url, ledger, limiter=None, **_):
        self.calls += 1
        if self.calls > self.fail_after:
            raise HttpFetchError(url, 429, "Too Many Requests")
        return FakeWiki()(url, ledger, limiter)


def test_a_page_already_in_the_corpus_is_not_fetched_again(store, raw, ledger):
    land_raw(store)
    fetch = FakeWiki()
    docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=fetch, pause_seconds=0)
    before = len(fetch.calls)
    docs.ingest_wikipedia(store, raw, ledger, "run-2", fetch=fetch, pause_seconds=0)
    assert len(fetch.calls) == before


def test_refresh_refetches_what_is_already_there(store, raw, ledger):
    land_raw(store)
    fetch = FakeWiki()
    docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=fetch, pause_seconds=0)
    before = len(fetch.calls)
    docs.ingest_wikipedia(store, raw, ledger, "run-2", fetch=fetch, refresh=True, pause_seconds=0)
    assert len(fetch.calls) > before


def test_one_refusal_is_recorded_and_the_walk_carries_on(store, raw, ledger):
    pages = [page(), page("circuit")]
    outcomes = docs.ingest_wikipedia(
        store, raw, ledger, "run-1", pages=pages, fetch=Refuses(1), pause_seconds=0
    )
    assert outcomes[0].key is not None
    assert "429" in str(outcomes[1].skipped)


def test_three_refusals_in_a_row_stop_the_walk(store, raw, ledger):
    pages = [page() for _ in range(10)]
    outcomes = docs.ingest_wikipedia(
        store, raw, ledger, "run-1", pages=pages, fetch=Refuses(0), pause_seconds=0
    )
    assert len(outcomes) < len(pages)
    assert "run it again later" in str(outcomes[-1].skipped)


def test_the_walk_spaces_itself_out(store, raw, ledger):
    waits = []
    pages = [page(), page("circuit")]
    docs.ingest_wikipedia(
        store,
        raw,
        ledger,
        "run-1",
        pages=pages,
        fetch=FakeWiki(),
        pause_seconds=2.0,
        sleep=waits.append,
    )
    assert waits == [2.0]


def one_page_pdf(lines):
    body = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    body += "".join(f"({line}) Tj T*\n" for line in lines)
    body += "ET"
    stream = body.encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + payload + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
    out += str(start).encode() + b"\n%%EOF\n"
    return bytes(out)


SPORTING = one_page_pdf(
    ["Article 40.1 The safety car may be deployed at the discretion of the race director."]
    + [
        f"Article 40.{number} Overtaking behind the safety car is not permitted."
        for number in range(2, 12)
    ]
)

REGULATION = docs.Regulation(
    season=2024,
    title="FIA 2024 Formula 1 Sporting Regulations, Issue 7",
    issued=date(2024, 7, 31),
    url="https://www.fia.com/sites/default/files/sporting.pdf",
)


class FakeFia:
    def __init__(self, body=SPORTING, status=200):
        self.body = body
        self.status = status
        self.calls = []

    def __call__(self, url, ledger, limiter=None, **_):
        self.calls.append(url)
        return Response(url=url, status=self.status, body=self.body, etag='"f1"', fetched_at=NOW)


def test_the_manifest_covers_both_regulations_of_every_season_in_the_lake():
    listed = {(item.season, "sporting" in item.title.lower()) for item in docs.regulations()}
    assert listed == {
        (season, sporting) for season in range(2021, 2026) for sporting in (True, False)
    }
    assert all(item.url.startswith("https://www.fia.com/") for item in docs.regulations())


def test_a_regulation_lands_verbatim_in_raw_and_as_text_in_the_corpus(store, raw, ledger):
    fetch = FakeFia()
    outcome = docs.ingest_regulation(store, raw, REGULATION, ledger, "run-1", fetch=fetch)
    assert outcome.key == (
        "docs/source=fia_docs/kind=regulation/"
        "fia-2024-formula-1-sporting-regulations-issue-7-2024-07-31.txt"
    )
    assert "Article 40.1" in store.get(outcome.key).decode()
    landed = [item.key for item in store.list("raw/source=fia_docs/season=2024/")]
    stored = next(key for key in landed if key.endswith(".pdf"))
    assert store.get(stored) == SPORTING
    attributes = json.loads(store.get(outcome.key + ".metadata.json"))["metadataAttributes"]
    assert attributes["issued"] == "2024-07-31"
    assert attributes["season"] == 2024


def test_a_regulation_with_no_text_layer_is_reported_rather_than_written(store, raw, ledger):
    fetch = FakeFia(one_page_pdf(["Scanned."]))
    outcome = docs.ingest_regulation(store, raw, REGULATION, ledger, "run-1", fetch=fetch)
    assert outcome.key is None
    assert "no text layer" in outcome.skipped
    assert docs.corpus(store) == []


def test_a_regulation_already_in_the_corpus_is_not_fetched_again(store, raw, ledger):
    fetch = FakeFia()
    for run in ("run-1", "run-2"):
        docs.ingest_regulations(
            store, raw, ledger, run, wanted=[REGULATION], fetch=fetch, pause_seconds=0
        )
    assert len(fetch.calls) == 1


def test_a_note_of_ours_is_not_filed_as_an_fia_document():
    assert docs.source_of("regulation") is Source.FIA_DOCS
    assert docs.source_of("methodology") is Source.CURATED
    assert docs.source_of("anything else") is Source.CURATED


def test_a_methodology_note_takes_its_title_from_the_first_line(store, tmp_path):
    (tmp_path / "clean-air.txt").write_text(
        "Clean-air race pace\n\nThe estimator refuses to guess."
    )
    outcomes = docs.ingest_methodology(store, tmp_path)
    assert [item.key for item in outcomes] == [
        "docs/source=curated/kind=methodology/clean-air-race-pace.txt"
    ]
    attributes = json.loads(store.get(outcomes[0].key + ".metadata.json"))["metadataAttributes"]
    assert attributes["source"] == "curated"
    assert attributes["title"] == "Clean-air race pace"


def test_an_unchanged_note_is_not_rewritten(store, tmp_path):
    (tmp_path / "note.txt").write_text("A note\n\nBody.")
    docs.ingest_methodology(store, tmp_path)
    again = docs.ingest_methodology(store, tmp_path)
    assert again[0].key is None
    assert again[0].skipped == "unchanged"


def test_an_edited_note_is_rewritten(store, tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("A note\n\nBody.")
    docs.ingest_methodology(store, tmp_path)
    path.write_text("A note\n\nBody, corrected.")
    assert docs.ingest_methodology(store, tmp_path)[0].key is not None
    assert "corrected" in store.get("docs/source=curated/kind=methodology/a-note.txt").decode()


def test_the_shipped_notes_all_carry_a_title_line():
    for path in sorted(docs.METHODOLOGY.glob("*.txt")):
        item, text = docs.methodology_note(path)
        assert item.title, path
        assert not item.title.endswith("."), path
        assert len(text) > docs.MIN_CHARACTERS, path
