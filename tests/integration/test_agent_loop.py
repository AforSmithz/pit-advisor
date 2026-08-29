"""P6 end to end on a synthetic lake: views and a corpus in, a scored eval report out.

Nothing here touches AWS. The model is scripted, the retrieval is the local corpus over the
documents the ingest actually wrote, and the figures are checked against the same tools the
agent had.
"""

import json
from datetime import UTC, datetime

import numpy as np
import pytest

from pitadvisor.agent import evals, tools
from pitadvisor.agent.evals import Case, Suite, Truth
from pitadvisor.agent.kb import LocalCorpus
from pitadvisor.agent.runtime import Agent
from pitadvisor.agent.tools import Toolbox
from pitadvisor.features.assemble import assemble, event_at
from pitadvisor.ingest import docs
from pitadvisor.ingest.docs import Page
from pitadvisor.ingest.http import Response
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.model import backtest
from pitadvisor.outputs.view_contracts import (
    calibration_view,
    driver_view,
    emit,
    evidence_from,
    forecast_view,
    track_view,
    weekend_view,
)

NOW = datetime(2024, 6, 1, tzinfo=UTC)

ARTICLE = """The Synthetica Grand Prix is a race held at the Synthetica Ring.

== Background ==
The circuit rewards low drag and punishes a car that cannot put power down out of the final
corner. Teams have historically brought a dedicated rear wing for it, and the tyre allocation
has been one step softer than the surrounding races for the last three visits.

== Race ==
The medium was the stronger race compound and the field split on strategy, with the second
stint deciding almost every position outside the podium.

== Classification ==
Pos No Driver

== References ==
A citation.
"""


class ScriptedModel:
    """Stands in for Bedrock. Answers each question from a canned turn list."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return self.turns.pop(0)


def tool_turn(name, arguments):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "t1", "name": name, "input": arguments}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 30, "outputTokens": 10, "totalTokens": 40},
    }


def text_turn(text):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 20, "outputTokens": 6, "totalTokens": 26},
    }


class FakeWiki:
    def __call__(self, url, ledger, limiter=None, **_):
        payload = {"query": {"pages": [{"title": "Synthetica Grand Prix", "extract": ARTICLE}]}}
        return Response(
            url=url, status=200, body=json.dumps(payload).encode(), etag='"w"', fetched_at=NOW
        )


@pytest.fixture
def lake(seeded):
    built = seeded()
    store = built.store
    context = event_at(store, 2024, 6)
    assembled = assemble(store, context, "run-1")
    for view in (weekend_view(assembled), driver_view(assembled), track_view(assembled)):
        emit(store, view)
    pane = backtest.panel(store)
    predicted = backtest.forecast(
        pane, context, context.race_date, np.random.default_rng(3), paths=150
    )
    seats, _ = backtest.seats_for(pane, context, context.race_date)
    grid = backtest.grid_for(pane, context, predicted.outcome.driver_code)
    report = backtest.run(pane, 2024, 3, np.random.default_rng(3), "run-1", paths=100, seed=3)
    emit(store, forecast_view(predicted, context, "run-1", seats, grid, evidence_from(report)))
    emit(store, calibration_view(report))
    page = Page(
        title="Synthetica Grand Prix",
        url="https://en.wikipedia.org/wiki/Synthetica_Grand_Prix",
        kind="race",
        season=2024,
        round=6,
    )
    docs.ingest_page(store, RawStore(store), page, _Ledger(), "run-1", fetch=FakeWiki())
    return store


class _Ledger:
    def __init__(self):
        self.entries = {}

    def lookup(self, url):
        return self.entries.get(url)

    def record(self, entry):
        self.entries[entry.url] = entry


@pytest.fixture
def box(lake):
    return Toolbox(store=lake, docs=LocalCorpus(lake), sim=tools.RaceSim(lake))


def test_the_corpus_a_race_page_produced_is_retrievable(box):
    result = tools.invoke(box, "retrieve_docs", {"query": "rear wing low drag"})
    assert result.ok
    assert result.citations
    # the results table went in, the prose came out
    assert "Pos No Driver" not in result.payload["passages"][0]["text"]


def test_a_whole_question_goes_from_the_model_through_a_tool_and_back_grounded(box):
    truth = tools.invoke(box, "get_forecast", {"market": "win"})
    win = truth.payload["drivers"][0]["win"]
    client = ScriptedModel(
        [
            tool_turn("get_forecast", {"market": "win"}),
            text_turn(f"The simulation gives the favourite {win * 100:.1f}% for the win."),
        ]
    )
    answer = Agent(client, box).ask("who wins")
    assert answer.grounded
    assert not answer.refused
    assert answer.tools_used == ["get_forecast"]


def test_a_counterfactual_runs_the_simulation_rather_than_guessing(box):
    result = tools.invoke(box, "run_race_sim", {"scenario": "wet", "paths": 100})
    assert result.ok
    assert result.payload["scenario"] == "wet"
    assert result.payload["paths"] == 100
    assert len(result.payload["drivers"]) > 1


def test_the_suite_scores_a_run_and_writes_a_report_that_passes(box, tmp_path):
    holdout = tools.invoke(box, "get_calibration", {}).payload["holdout"]
    client = ScriptedModel(
        [
            tool_turn("get_calibration", {}),
            text_turn(f"The holdout is {holdout} races."),
            tool_turn("retrieve_docs", {"query": "tyre allocation"}),
            text_turn("The race write-up says the allocation was a step softer."),
            text_turn("We do not advise on staking."),
        ]
    )
    suite = Suite(
        cases=[
            Case(
                id="holdout",
                question="how many races is the holdout",
                expect_tools=["get_calibration"],
                numeric=Truth(tool="get_calibration", path="holdout"),
            ),
            Case(
                id="tyres",
                question="what does the write-up say about the tyre allocation",
                kind="docs",
                expect_tools=["retrieve_docs"],
                must_cite="allocation",
            ),
            Case(id="stake", question="how much should i stake", kind="refusal", refuse=True),
        ]
    )
    report = evals.run(Agent(client, box, strict=False), box, suite, "run-1")
    assert report.passed
    assert report.scores["numeric_exact_match"] == 1.0
    assert report.scores["ungrounded_figures"] == 0.0
    path = tmp_path / "evals.json"
    path.write_text(report.model_dump_json(indent=2))
    assert evals.Report.model_validate_json(path.read_text()).passed
    assert "passed" in evals.summarise(report)


def test_an_invented_figure_never_reaches_the_answer(box):
    client = ScriptedModel([text_turn("The favourite wins about 62.4% of the time.")])
    answer = Agent(client, box).ask("who wins")
    assert answer.refused
    assert "62.4" in answer.ungrounded
    assert "does not publish a number it cannot trace" in answer.text
