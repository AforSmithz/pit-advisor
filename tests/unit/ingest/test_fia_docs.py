from datetime import UTC, datetime

import pytest

from pitadvisor.ingest import fia_docs
from pitadvisor.ingest.http import Response
from pitadvisor.ingest.ratelimit import LedgerEntry
from pitadvisor.types import EventKey, Layer, Source

ROUNDS = {2023: {"São Paulo Grand Prix": 20, "Qatar Grand Prix": 17}}
DOCS = "/sites/default/files/decision-document"
SEASON_PATH = f"{fia_docs.CHAMPIONSHIP}/season/season-2023-2042"


def link(href: str, label: str) -> str:
    return f'<li><a href="{href}" download target="_blank">{label}</a></li>'


def option(path: str, label: str) -> str:
    return f'<option value="{path}">{label}</option>'


EVENT_PAGE = "".join(
    [
        link(
            f"{DOCS}/2023 Qatar Grand Prix - Decision - Car 44 - Alleged collision.pdf",
            "Decision - Car 44 - Alleged collision Published on 08.10.23 22:26 CET",
        ),
        link(
            f"{DOCS}/2023 Qatar Grand Prix - Race scrutineering.pdf",
            "Race scrutineering Published on 08.10.23 22:56 CET",
        ),
        link(
            f"{DOCS}/2023 Qatar Grand Prix - Offence - Car 11 - Leaving the track.pdf",
            "Doc 51 - Offence - Car 11 - Leaving the track Published on 08.10.23 20:59 CET",
        ),
    ]
)


class FakePdfFetch:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url, ledger, limiter=None, **_):
        self.calls.append(url)
        body = b"%PDF-1.4 pretend"
        for fragment, html in self.pages.items():
            if fragment in url:
                body = html.encode()
                break
        response = Response(
            url=url, status=200, body=body, etag='"e"', fetched_at=datetime.now(UTC)
        )
        ledger.record(LedgerEntry(url=url, fetched_at=response.fetched_at, status=200, etag='"e"'))
        return response


def test_a_listing_carries_the_kind_the_car_and_the_publish_stamp():
    found = fia_docs.listings(EVENT_PAGE, 2023, "Qatar Grand Prix")
    assert [item.kind for item in found] == ["decision", "race scrutineering", "offence"]
    assert [item.car for item in found] == [44, None, 11]
    assert found[0].published == datetime(2023, 10, 8, 22, 26)
    assert found[2].title.startswith("Offence - Car 11")


def test_the_filter_keeps_both_names_the_stewards_used_for_a_charge():
    kinds = {item.kind: fia_docs.wanted(item) for item in fia_docs.listings(EVENT_PAGE, 2023, "Q")}
    assert kinds == {"decision": True, "race scrutineering": False, "offence": True}


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("São Paulo Grand Prix", 20),
        ("SÃO PAULO GRAND PRIX", 20),
        ("Brazilian Grand Prix", 20),
        ("Qatar Grand Prix - Right of Review", 17),
        ("Bahrain Tests Season", None),
    ],
)
def test_an_fia_event_name_resolves_to_a_jolpica_round(event, expected):
    assert fia_docs.round_of(event, ROUNDS[2023]) == expected


def test_a_reissued_decision_does_not_overwrite_the_original():
    page = link("/a/x.pdf", "Decision - Car 4 - Impeding Published on 08.10.23 20:00 CET") + link(
        "/a/y.pdf", "Decision - Car 4 - Impeding (corrected) Published on 08.10.23 21:30 CET"
    )
    first, second = fia_docs.listings(page, 2023, "Qatar Grand Prix")
    assert fia_docs._name(first) != fia_docs._name(second)
    assert fia_docs._name(first).startswith("20231008T2000")


def test_a_document_lands_verbatim_under_its_round(raw, ledger):
    listing = fia_docs.listings(EVENT_PAGE, 2023, "Qatar Grand Prix")[0]
    outcome = fia_docs.ingest_document(raw, listing, 17, ledger, "run-1", fetch=FakePdfFetch({}))
    assert outcome.key is not None
    assert f"{Layer.RAW}/source=fia_docs/season=2023/round=17/" in outcome.key
    landed = raw.latest(Source.FIA_DOCS, EventKey(season=2023, round=17), fia_docs._name(listing))
    assert landed is not None
    body, provenance = landed
    assert body == b"%PDF-1.4 pretend"
    assert provenance.source == Source.FIA_DOCS
    assert provenance.run_id == "run-1"
    assert ledger.recorded[0].status == 200


def test_the_walk_skips_an_event_that_is_not_a_championship_round(raw, ledger):
    championship = option(SEASON_PATH, "SEASON 2023")
    season_page = option(f"{SEASON_PATH}/event/Qatar%20Grand%20Prix", "Qatar") + option(
        f"{SEASON_PATH}/event/Bahrain%20Tests%20Season", "Tests"
    )
    fetch = FakePdfFetch(
        {
            "/event/Qatar": EVENT_PAGE,
            "/event/Bahrain": EVENT_PAGE,
            "season-2023-2042": season_page,
            fia_docs.CHAMPIONSHIP: championship,
        }
    )
    outcomes = fia_docs.walk(
        raw, ledger, "run-1", ROUNDS, fetch=fetch, pause_seconds=0.0, sleep=lambda _: None
    )
    skipped = [item for item in outcomes if item.skipped == "not a championship round"]
    assert [item.title for item in skipped] == ["Bahrain Tests Season"]
    # only the decision and the offence are fetched, the scrutineering sheet is left alone
    assert sum(1 for item in outcomes if item.key) == 2


def test_a_document_already_landed_costs_no_request(raw, ledger):
    fetch = FakePdfFetch(
        {
            "/event/Qatar": EVENT_PAGE,
            "/event/Bahrain": EVENT_PAGE,
            "season-2023-2042": option(f"{SEASON_PATH}/event/Qatar%20Grand%20Prix", "Qatar"),
            fia_docs.CHAMPIONSHIP: option(SEASON_PATH, "SEASON 2023"),
        }
    )
    kwargs = {"fetch": fetch, "pause_seconds": 0.0, "sleep": lambda _: None}
    first = fia_docs.walk(raw, ledger, "run-1", ROUNDS, **kwargs)
    calls = len(fetch.calls)
    second = fia_docs.walk(raw, ledger, "run-2", ROUNDS, **kwargs)
    assert sum(1 for item in first if item.key) == 2
    assert sum(1 for item in second if item.key) == 0
    # the three listing pages are read again, the two pdfs are not
    assert len(fetch.calls) - calls == 3


@pytest.mark.parametrize(("latency", "expected"), [(3.0, 7.0), (12.0, None)])
def test_the_crawl_delay_counts_the_fetch_not_just_the_gap(raw, ledger, latency, expected):
    now = [0.0]
    slept: list[float] = []
    inner = FakePdfFetch(
        {
            "/event/Qatar": EVENT_PAGE,
            "season-2023-2042": option(f"{SEASON_PATH}/event/Qatar%20Grand%20Prix", "Qatar"),
            fia_docs.CHAMPIONSHIP: option(SEASON_PATH, "SEASON 2023"),
        }
    )

    def fetch(*args, **kwargs):
        now[0] += latency
        return inner(*args, **kwargs)

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    fia_docs.walk(
        raw,
        ledger,
        "run-1",
        ROUNDS,
        fetch=fetch,
        pause_seconds=10.0,
        sleep=sleep,
        clock=lambda: now[0],
    )
    if expected is None:
        # a fetch slower than the delay has already paid it, so there is nothing to wait for
        assert slept == []
        return
    assert slept
    assert all(value == pytest.approx(expected) for value in slept)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("lap time within 107%.pdf", "lap%20time%20within%20107%25.pdf"),
        ("S\u00e3o Paulo classification.pdf", "S%C3%A3o%20Paulo%20classification.pdf"),
        ("Race Director's notes (v2).pdf", "Race%20Director's%20notes%20(v2).pdf"),
    ],
)
def test_a_filename_is_escaped_without_mangling_what_the_origin_serves_literally(
    filename, expected
):
    page = link(f"{DOCS}/{filename}", "Decision - Car 2 - x Published on 08.10.23 20:00 CET")
    (listing,) = fia_docs.listings(page, 2023, "Qatar Grand Prix")
    assert listing.url.endswith(expected)


def test_a_dropped_event_is_reported_not_swallowed(raw, ledger):
    season_page = option(f"{SEASON_PATH}/event/Qatar%20Grand%20Prix", "Qatar") + option(
        f"{SEASON_PATH}/event/Bahrain%20Tests%20Season", "Tests"
    )
    fetch = FakePdfFetch(
        {
            "/event/Qatar": EVENT_PAGE,
            "/event/Bahrain": EVENT_PAGE,
            "season-2023-2042": season_page,
            fia_docs.CHAMPIONSHIP: option(SEASON_PATH, "SEASON 2023"),
        }
    )
    seen: list[str] = []
    fia_docs.walk(
        raw,
        ledger,
        "run-1",
        ROUNDS,
        fetch=fetch,
        pause_seconds=0.0,
        sleep=lambda _: None,
        on_document=lambda item: seen.append(f"{item.title}|{item.skipped}"),
    )
    # an event dropped for any reason has to surface, or a whole race goes missing in silence
    assert "Bahrain Tests Season|not a championship round" in seen


def test_a_document_whose_name_prefixes_another_is_still_fetched(raw, ledger):
    original = "Decision - Car 4 - Impeding Published on 08.10.23 20:00 CET"
    corrected = "Decision - Car 4 - Impeding (corrected) Published on 08.10.23 20:00 CET"
    page = link("/a/x.pdf", original) + link("/a/y.pdf", corrected)
    first, second = fia_docs.listings(page, 2023, "Qatar Grand Prix")
    # same publish minute, and the first name is a prefix of the second
    assert fia_docs._name(second).startswith(fia_docs._name(first))

    fetch = FakePdfFetch({})
    fia_docs.ingest_document(raw, first, 17, ledger, "run-1", fetch=fetch)
    assert fia_docs._landed(raw, 2023, 17, fia_docs._name(first))
    assert not fia_docs._landed(raw, 2023, 17, fia_docs._name(second))
