from datetime import datetime
from typing import Any

from pitadvisor.incidents.extract import TOOL, extract
from pitadvisor.incidents.parse import Book

RAW = (
    "From The Stewards\n"
    "Document 62\n"
    "Date 15 September 2024\n"
    "Time 19:10\n"
    "2024 SAMPLE GRAND PRIX\n"
    "The Stewards, having received a report from the Race Director, have considered the\n"
    "following matter and determine the following:\n"
    "Session Race\n"
    "Fact The cars below did not use the track at turn 4.\n"
    "No Car Driver Competitor\n"
    "1 7 Jo Mercier Cobalt Racing\n"
    "2 12 Rae Okonkwo Halden Motorsport\n"
    "Infringement Breach of Appendix L Chapter IV Article 2 c) of the FIA International\n"
    "   Sporting Code and Article 33.3 of the FIA Formula One Sporting Regulations.\n"
    "Decision Deletion of the lap times shown.\n"
)

CHARGE = (
    "Breach of Appendix L Chapter IV Article 2 c) of the FIA International Sporting Code "
    "and Article 33.3 of the FIA Formula One Sporting Regulations."
)


def entry(**over: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "car": 7,
        "driver": "Jo Mercier",
        "competitor": "Cobalt Racing",
        "session": "Race",
        "fact": "The cars below did not use the track at turn 4.",
        "charge": CHARGE,
        "outcome": "Deletion of the lap times shown.",
        "reason": None,
    }
    found.update(over)
    return found


class FakeBedrock:
    def __init__(self, entries: list[dict[str, Any]] | None, tool: str = TOOL) -> None:
        self.entries = entries
        self.tool = tool
        self.request: dict[str, Any] = {}

    def converse(self, **request: Any) -> dict[str, Any]:
        self.request = request
        content: list[dict[str, Any]] = [{"text": "recording"}]
        if self.entries is not None:
            content.append({"toolUse": {"name": self.tool, "input": {"entries": self.entries}}})
        return {
            "output": {"message": {"content": content}},
            "usage": {"inputTokens": 1800, "outputTokens": 400},
        }


def test_one_entry_per_car_becomes_one_decision():
    client = FakeBedrock(
        [entry(), entry(car=12, driver="Rae Okonkwo", competitor="Halden Motorsport")]
    )
    found = extract(client, "model", RAW)
    assert found.clean
    assert [decision.car for decision in found.decisions] == [7, 12]
    assert [decision.driver for decision in found.decisions] == ["Jo Mercier", "Rae Okonkwo"]


def test_the_header_the_parser_already_reads_is_not_asked_of_the_model():
    found = extract(FakeBedrock([entry()]), "model", RAW)
    assert found.decisions[0].document == 62
    assert found.decisions[0].issued == datetime(2024, 9, 15, 19, 10)


def test_a_charge_is_still_split_into_citations():
    found = extract(FakeBedrock([entry()]), "model", RAW)
    assert [(article.code, article.book) for article in found.decisions[0].articles] == [
        ("Appendix L Chapter IV Article 2 c)", Book.ISC),
        ("Article 33.3", Book.SPORTING),
    ]


def test_a_value_the_document_does_not_contain_is_reported():
    client = FakeBedrock([entry(outcome="10 second time penalty."), entry(car=12)])
    found = extract(client, "model", RAW)
    assert not found.clean
    assert found.unverified == ["0.outcome"]


def test_a_quote_that_wraps_across_an_indented_line_still_verifies():
    # the charge in the source wraps and the continuation is indented, so a single spaced copy
    # of it only matches once the join squeezes the run of spaces it leaves behind
    found = extract(FakeBedrock([entry()]), "model", RAW)
    assert found.unverified == []


def test_a_response_with_no_tool_use_is_an_error():
    found = extract(FakeBedrock(None), "model", RAW)
    assert not found.clean
    assert found.error == "the model recorded no entries"
    assert found.decisions == []


def test_the_model_is_made_to_use_the_tool():
    client = FakeBedrock([entry()])
    extract(client, "model", RAW)
    assert client.request["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL}}
    assert client.request["inferenceConfig"]["temperature"] == 0.0


def test_usage_comes_back_so_a_run_can_be_costed():
    found = extract(FakeBedrock([entry()]), "model", RAW)
    assert (found.input_tokens, found.output_tokens) == (1800, 400)


def test_a_quote_is_replaced_by_the_substring_of_the_document_it_points_at():
    curly = RAW.replace("did not use the track", "did not use the Director\u2019s track")
    client = FakeBedrock([entry(fact="The cars below did not use the Director's track at turn 4.")])
    found = extract(client, "model", curly)
    assert found.clean
    assert (
        found.decisions[0].fact == "The cars below did not use the Director\u2019s track at turn 4."
    )


def test_a_paraphrase_is_dropped_rather_than_stored():
    client = FakeBedrock([entry(outcome="Lap times were deleted")])
    found = extract(client, "model", RAW)
    assert found.unverified == ["0.outcome"]
    assert found.decisions[0].outcome is None
    assert found.decisions[0].fact is not None
