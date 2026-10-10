import io
import json
import urllib.error
from datetime import UTC, date, datetime

import polars as pl
import pytest

from pitadvisor.ingest import typesafe
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.quality.checks import read_table
from pitadvisor.types import Layer, Source

STAMP = {"run_id": "run-1", "ingested_at": datetime(2025, 6, 1, tzinfo=UTC)}


def updates():
    return pl.DataFrame(
        {
            "season": [2025] * 4,
            "round": [9] * 4,
            "document_name": ["car presentation submissions"] * 4,
            "constructor_id": ["mclaren", "mclaren", "mclaren", "ferrari"],
            "team": ["McLaren", "McLaren", "McLaren", "Ferrari"],
            "ordinal": [1, 2, 3, 0],
            "category": ["performance", "reliability", "performance", "none"],
            "reason": ["Performance - Flow Conditioning", "Reliability", "Performance", ""],
            "text": ["New floor fences.", "Revised cooling louvres.", "Trimmed flap.", ""],
        }
    )


def races():
    return pl.DataFrame(
        {
            "season": [2025],
            "round": [9],
            "race_name": ["Spanish Grand Prix"],
            "circuit_id": ["catalunya"],
            "race_date": [date(2025, 6, 1)],
        }
    )


def answer(body, effect=1.0, package=0.2, model=typesafe.MODEL):
    answers = {}
    for name, question in body["questions"].items():
        if question["type"] == "score":
            answers[name] = {
                "type": "score",
                "score": effect,
                "legend": {str(i): text for i, text in enumerate(question["criteria"])},
                "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0},
                "confidence": 0.9,
            }
        else:
            answers[name] = {"type": "noul", "noul": package}
    return json.dumps(
        {"model": model, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}
    ).encode()


def test_one_request_per_team_covers_only_its_performance_updates():
    rows = updates().filter(pl.col("constructor_id") == "mclaren").to_dicts()
    performance = [row for row in rows if row["category"] == "performance"]
    body = typesafe.request_body("Spanish Grand Prix", "catalunya", "McLaren", performance)
    assert body["model"] == typesafe.MODEL
    assert set(body["questions"]) == {"effect_1", "package_1", "effect_3", "package_3"}
    assert body["questions"]["effect_3"]["criteria"] == list(typesafe.LEVELS)
    assert "`updates[1]`" in body["questions"]["effect_3"]["instructions"]
    assert [item["text"] for item in body["state"]["updates"]] == [
        "New floor fences.",
        "Trimmed flap.",
    ]
    assert typesafe.name_for(body) == typesafe.name_for(json.loads(json.dumps(body)))


def test_responses_land_once_and_replay_without_the_api(store):
    asked = []

    def ask(body):
        asked.append(body)
        return answer(body, effect=1.5)

    raw = RawStore(store)
    first = typesafe.score(raw, updates(), races(), STAMP, ask)
    assert first == (1, 2, 0)
    assert len(list(store.list(f"{Layer.RAW}/source={Source.TYPESAFE}/"))) == 2  # body and meta
    again = typesafe.score(raw, updates(), races(), STAMP, None)
    assert again == (0, 2, 0)
    assert len(asked) == 1
    frame = read_table(store, Layer.BRONZE, "car_update_magnitudes")
    assert sorted(frame["ordinal"].to_list()) == [1, 3]
    assert frame["magnitude"].to_list() == [1.5, 1.5]
    assert set(frame["model"].to_list()) == {typesafe.MODEL}


def test_without_a_landed_response_or_the_api_nothing_is_written(store):
    assert typesafe.score(RawStore(store), updates(), races(), STAMP, None) == (0, 0, 0)
    assert read_table(store, Layer.BRONZE, "car_update_magnitudes") is None


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


def refusal(code):
    return urllib.error.HTTPError(typesafe.ENDPOINT, code, "no", {}, io.BytesIO(b"{}"))


def opener_of(*steps):
    seen = []

    def opener(request, timeout):
        seen.append(request)
        step = steps[len(seen) - 1]
        if isinstance(step, Exception):
            raise step
        return Reply(step)

    return opener, seen


def test_an_overloaded_answer_is_retried_with_backoff():
    body = typesafe.request_body("X", "y", "Z", updates().head(1).to_dicts())
    opener, seen = opener_of(refusal(529), refusal(429), answer(body))
    waits = []
    payload = typesafe.call(body, "test-key", opener, waits.append)
    assert json.loads(payload)["model"] == typesafe.MODEL
    assert waits == [1.0, 2.0]
    request = seen[0]
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-key"


@pytest.mark.parametrize("code", [401, 422])
def test_a_bad_request_is_not_retried(code):
    body = typesafe.request_body("X", "y", "Z", updates().head(1).to_dicts())
    opener, seen = opener_of(refusal(code))
    with pytest.raises(typesafe.JevError) as raised:
        typesafe.call(body, "test-key", opener, lambda _: None)
    assert raised.value.status == code
    assert len(seen) == 1


def test_an_answer_from_another_model_version_is_refused():
    body = typesafe.request_body("X", "y", "Z", updates().head(1).to_dicts())
    opener, _ = opener_of(answer(body, model="jev-2.0.0"))
    with pytest.raises(typesafe.JevError, match="pinned"):
        typesafe.call(body, "test-key", opener, lambda _: None)
