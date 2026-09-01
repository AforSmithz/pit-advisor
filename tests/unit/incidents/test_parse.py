from datetime import datetime

from pitadvisor.incidents.parse import parse, verify

NB = "\u00a0"


def document(charge_label: str = "Infringement") -> str:
    # the pdf text layer joins words with non-breaking spaces and wraps values mid sentence,
    # so a fixture that uses plain spaces would not exercise what the parser has to survive
    return (
        "From The Stewards\n"
        "To The Team Manager,\n"
        "Cobalt Racing\n"
        "Document 41\n"
        "Date 14 May 2023\n"
        "Time 18:22\n"
        "2023 SAMPLE GRAND PRIX\n"
        "12 - 14 May 2023\n"
        "The Stewards, having received a report from the Race Director, summoned "
        "(documents 27 & 28)\nand heard from the drivers, determine the following:\n"
        f"No{NB}/{NB}Driver 7{NB}-{NB}Jo{NB}Mercier\n"
        "Competitor Cobalt Racing\n"
        "Time 15:04\n"
        "Session Race\n"
        "Fact Causing a collision with Car 12 at turn 4.\n"
        f"{charge_label} Breach of Article 33.4 of the FIA Formula One Sporting\n"
        "Regulations.\n"
        "Decision 10 second time penalty and 2 penalty points\n"
        "(This is the driver's 3rd penalty point of the season)\n"
        "Reason The Stewards reviewed video evidence and heard from both drivers.\n"
        "The driver of Car 7 was wholly at fault for the contact.\n"
        "Competitors are reminded that they have the right to appeal certain decisions of the\n"
        "Stewards, in accordance with Article 15 of the FIA International Sporting Code.\n"
        "The Stewards\n"
        "Ada Fournier Kit Rasmussen\n"
    )


PROSE = (
    "From The Stewards\n"
    "To All Teams, All Officials\n"
    "Document 43\n"
    "Date 14 May 2023\n"
    "Time 12:42\n"
    "2023 SAMPLE GRAND PRIX\n"
    "The Stewards have received a request from Cobalt Racing to withdraw Car 7 from the\n"
    "Competition on the grounds of force majeure. This request is approved.\n"
)


def test_the_field_block_gives_up_the_driver_session_and_outcome():
    found = parse(document())
    assert found.document == 41
    assert found.issued == datetime(2023, 5, 14, 18, 22)
    assert found.car == 7
    assert found.driver == "Jo Mercier"
    assert found.competitor == "Cobalt Racing"
    assert found.session == "Race"
    assert found.outcome.startswith("10 second time penalty and 2 penalty points")


def test_the_charge_label_changed_name_between_seasons():
    for label in ("Offence", "Infringement"):
        found = parse(document(label))
        assert found.charge is not None
        assert [article.code for article in found.articles] == ["Article 33.4"]


def test_a_citation_keeps_the_wording_the_stewards_used():
    raw = document().replace(
        "Breach of Article 33.4 of the FIA Formula One Sporting\nRegulations.",
        "Breach of Appendix L Ch IV Art 1, Appendix H 2.5.4.1.b of the FIA International "
        "Sporting Code.",
    )
    assert [article.code for article in parse(raw).articles] == [
        "Appendix L Ch IV Art 1",
        "Appendix H 2.5.4.1.b",
    ]


def test_the_reason_stops_before_the_appeal_boilerplate():
    reason = parse(document()).reason
    assert reason is not None
    assert reason.endswith("wholly at fault for the contact.")
    assert "right to appeal" not in reason


def test_the_summons_documents_come_out_of_the_preamble():
    assert parse(document()).summoned == [27, 28]


def test_every_stored_span_is_found_in_the_source():
    raw = document()
    assert verify(parse(raw), raw) == []


def test_a_document_with_no_field_block_is_flagged_for_the_model():
    found = parse(PROSE)
    assert not found.structured
    assert found.document == 43
    assert found.issued == datetime(2023, 5, 14, 12, 42)
    assert found.car is None
