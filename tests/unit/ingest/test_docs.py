import json
from datetime import UTC, datetime

import pytest

from pitadvisor.ingest import docs
from pitadvisor.ingest.docs import Curated, Page
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
        store, source, Curated(title="Sporting Regulations 2025", kind="regulation", season=2025)
    )
    assert key == "docs/source=fia_docs/kind=regulation/sporting-regulations-2025.txt"
    assert json.loads(store.get(key + ".metadata.json"))["metadataAttributes"]["season"] == 2025


def test_the_corpus_listing_leaves_the_sidecars_out(store, raw, ledger):
    land_raw(store)
    docs.ingest_wikipedia(store, raw, ledger, "run-1", fetch=FakeWiki())
    assert all(not key.endswith(".metadata.json") for key in docs.corpus(store))


def test_wikipedia_is_a_source_the_raw_layout_knows_about():
    assert Source.WIKIPEDIA in set(Source)


@pytest.mark.parametrize("kind", ["race", "circuit"])
def test_the_key_says_which_kind_of_page_it_is(kind):
    assert f"kind={kind}" in docs.doc_key(page(kind))
