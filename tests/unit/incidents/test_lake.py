import json
from datetime import UTC, datetime

from pitadvisor.incidents import lake
from pitadvisor.incidents.parse import Article, Book, Decision

RAW_KEY = "raw/source=fia_docs/season=2024/round=17/20240915T1710-infringement-race-x-2026.pdf"
STAMP = {"run_id": "run-1", "ingested_at": datetime(2026, 9, 2, tzinfo=UTC)}


def reading(**over):
    found = {
        "raw_key": RAW_KEY,
        "kind": "infringement",
        "read_by": lake.EXTRACTED,
        "decisions": [
            Decision(document=62, car=1, driver="Max Verstappen", outcome="Lap time deleted."),
            Decision(
                document=62,
                car=31,
                driver="Esteban Ocon",
                charge="Breach of Article 33.3 of the FIA Formula One Sporting Regulations.",
                articles=[
                    Article(
                        code="Article 33.3",
                        regulation="FIA Formula One Sporting Regulations",
                        book=Book.SPORTING,
                    )
                ],
            ),
        ],
        "unverified": ["1.reason"],
    }
    found.update(over)
    return lake.Reading(**found)


def test_the_kind_comes_off_the_document_name():
    assert lake.kind_of(RAW_KEY) == "infringement"
    assert (
        lake.kind_of("raw/source=fia_docs/season=2024/round=17/x-final-race-classification.pdf")
        is None
    )


def test_the_cache_key_mirrors_the_raw_key():
    assert lake.cache_key(RAW_KEY) == (
        "cache/incidents/source=fia_docs/season=2024/round=17/"
        "20240915T1710-infringement-race-x-2026.pdf.json"
    )


def test_each_car_is_its_own_row_with_an_ordinal():
    incidents = lake.rows(reading(), 2024, 17, STAMP).incidents
    assert [(row.entry, row.car) for row in incidents] == [(0, 1), (1, 31)]
    assert {row.season for row in incidents} == {2024}
    assert {row.read_by for row in incidents} == {lake.EXTRACTED}


def test_an_unverified_field_lands_on_the_entry_it_belongs_to():
    incidents = lake.rows(reading(), 2024, 17, STAMP).incidents
    assert incidents[0].unverified == []
    assert incidents[1].unverified == ["reason"]


def test_citations_come_out_as_their_own_rows():
    articles = lake.rows(reading(), 2024, 17, STAMP).articles
    assert [(row.entry, row.code, row.book) for row in articles] == [
        (1, "Article 33.3", "sporting")
    ]
    assert articles[0].raw_key == RAW_KEY


def test_a_row_carries_the_document_it_was_read_from():
    incidents = lake.rows(reading(), 2024, 17, STAMP).incidents
    assert {row.raw_key for row in incidents} == {RAW_KEY}
    assert {row.document for row in incidents} == {62}


def test_the_last_record_for_a_key_wins_and_a_failure_is_dropped():
    lines = [
        {
            "key": RAW_KEY,
            "decisions": [],
            "unverified": [],
            "error": "throttled",
            "input_tokens": 0,
            "output_tokens": 0,
        },
        {
            "key": RAW_KEY,
            "decisions": [Decision(document=62).model_dump(mode="json")],
            "unverified": [],
            "error": None,
            "input_tokens": 10,
            "output_tokens": 4,
        },
        {
            "key": RAW_KEY.replace("round=17", "round=18"),
            "decisions": [],
            "unverified": [],
            "error": "the response ran out of room",
            "input_tokens": 0,
            "output_tokens": 0,
        },
    ]
    found = lake.read_jsonl("\n".join(json.dumps(line) for line in lines))
    assert [item.raw_key for item in found] == [RAW_KEY]
    assert found[0].input_tokens == 10


def test_a_sanction_row_comes_off_the_outcome():
    found = reading(
        decisions=[
            Decision(
                document=62,
                car=1,
                outcome=(
                    "10 second time penalty. 2 penalty points (total of 4 for the 12 month period)."
                ),
            )
        ],
        unverified=[],
    )
    imposed = lake.rows(found, 2024, 17, STAMP).sanctions
    assert [
        (row.ordinal, row.kind, row.seconds, row.points, row.points_total) for row in imposed
    ] == [
        (0, "time_penalty", 10, None, None),
        (1, "penalty_points", None, 2, 4),
    ]
