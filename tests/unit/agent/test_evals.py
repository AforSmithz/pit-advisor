import json
from pathlib import Path

import pytest
import yaml

from pitadvisor.agent import evals, tools
from pitadvisor.agent.evals import Case, Suite, SuiteError, Truth
from pitadvisor.agent.runtime import Agent, Answer, ToolCall
from pitadvisor.agent.tools import Toolbox
from tests.unit.agent.test_runtime import FakeBedrock, said, wants


@pytest.fixture
def box(views):
    return Toolbox(store=views)


def written(tmp_path, payload):
    path = tmp_path / "suite.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def answer(text, calls=(), ungrounded=()):
    return Answer(
        text=text,
        question="q",
        calls=list(calls),
        stop_reason="end_turn",
        iterations=1,
        usage={},
        ungrounded=list(ungrounded),
    )


def call(name="get_forecast", ok=True):
    return ToolCall(name=name, arguments={}, ok=ok)


def test_the_shipped_golden_set_parses_and_is_the_size_the_plan_asked_for():
    suite = evals.load(Path("evals/golden.yaml"))
    assert len(suite.cases) >= 60
    assert {case.kind for case in suite.cases} >= {"lookup", "marts", "docs", "refusal"}


def test_every_case_in_the_golden_set_has_something_to_score():
    for case in evals.load(Path("evals/golden.yaml")).cases:
        assert case.expect_tools or case.refuse, case.id


def test_a_suite_that_loosens_the_gate_is_refused(tmp_path):
    path = written(tmp_path, {"thresholds": {"tool_selection": 0.5}, "cases": []})
    with pytest.raises(SuiteError) as caught:
        evals.load(path)
    assert "loosens the gate" in str(caught.value)


def test_a_suite_that_tightens_the_gate_is_allowed(tmp_path):
    path = written(tmp_path, {"thresholds": {"tool_selection": 0.99}, "cases": []})
    assert evals.load(path).thresholds["tool_selection"] == 0.99


def test_allowing_one_ungrounded_figure_is_loosening(tmp_path):
    path = written(tmp_path, {"thresholds": {"ungrounded_figures": 1.0}, "cases": []})
    with pytest.raises(SuiteError):
        evals.load(path)


def test_a_missing_suite_says_so(tmp_path):
    with pytest.raises(SuiteError):
        evals.load(tmp_path / "nothing.yaml")


def test_ground_truth_comes_from_the_same_tool_the_agent_has(box):
    value = evals.expected_value(box, Truth(tool="get_calibration", path="holdout"))
    assert value == 3


def test_ground_truth_can_come_from_a_mart_query(box, tmp_path):
    import duckdb

    path = tmp_path / "m.duckdb"
    connection = duckdb.connect(str(path))
    connection.sql("create table gold_race_results as select 7 as wins")
    connection.close()
    marts = tools.DuckDBMarts(path)
    truth = Truth(sql="select wins from gold_race_results", column="wins")
    assert evals.expected_value(Toolbox(store=box.store, marts=marts), truth) == 7


def test_a_ground_truth_that_cannot_be_resolved_is_a_suite_error(box):
    with pytest.raises(SuiteError):
        evals.expected_value(
            box, Truth(tool="get_driver_form", arguments={"driver_code": "ZZZ"}, path="form.value")
        )


def test_a_numeric_answer_matches_however_the_model_rendered_it(box):
    case = Case(id="c", question="q", numeric=Truth(tool="get_calibration", path="holdout"))
    assert evals.score(case, answer("The holdout is 3 races."), box).numeric_ok


def test_a_numeric_answer_that_misses_the_figure_fails(box):
    case = Case(id="c", question="q", numeric=Truth(tool="get_calibration", path="holdout"))
    assert not evals.score(case, answer("The holdout is 42 races."), box).numeric_ok


def test_tool_selection_wants_the_expected_tool_among_those_used(box):
    case = Case(id="c", question="q", expect_tools=["get_forecast"])
    assert evals.score(case, answer("x", [call("get_forecast")]), box).tool_ok
    assert not evals.score(case, answer("x", [call("get_weather")]), box).tool_ok


def test_a_case_expecting_no_tool_fails_when_a_tool_ran(box):
    case = Case(id="c", question="q", refuse=True)
    assert not evals.score(case, answer("no", [call()]), box).tool_ok


def test_a_refusal_is_scored_on_what_the_answer_says(box):
    case = Case(id="c", question="q", refuse=True)
    assert evals.score(case, answer("We do not have that."), box).refusal_ok
    assert not evals.score(case, answer("Sure, about 40 percent."), box).refusal_ok


def test_a_withheld_answer_counts_as_a_refusal(box):
    case = Case(id="c", question="q", refuse=True)
    withheld = answer("Withheld.", ungrounded=["41.7"]).model_copy(update={"refused": True})
    assert evals.score(case, withheld, box).refusal_ok


def test_a_citation_case_wants_the_document_named_in_the_answer(box):
    case = Case(id="c", question="q", expect_tools=["retrieve_docs"], must_cite="sporting")
    good = answer("The Sporting Regulations say so.", [call("retrieve_docs")])
    assert evals.score(case, good, box).citation_ok
    assert not evals.score(case, answer("It just does.", [call("retrieve_docs")]), box).citation_ok


def test_the_gate_fails_on_a_single_ungrounded_figure():
    measured = {"tool_selection": 1.0, "ungrounded_figures": 1.0}
    assert not evals.passes(measured, evals.GATE)


def test_the_gate_passes_when_every_threshold_is_met():
    measured = {
        "numeric_exact_match": 0.96,
        "retrieval_hit_rate": 0.91,
        "tool_selection": 0.95,
        "ungrounded_figures": 0.0,
    }
    assert evals.passes(measured, evals.GATE)


def test_a_missing_measurement_does_not_fail_the_gate():
    assert evals.passes({"tool_selection": 1.0, "ungrounded_figures": 0.0}, evals.GATE)


def test_the_whole_suite_runs_and_reports(box):
    result = tools.invoke(box, "get_calibration", {})
    holdout = result.payload["holdout"]
    client = FakeBedrock(
        wants("get_calibration", {}),
        said(f"The holdout is {holdout} races."),
        said("We do not have betting advice."),
    )
    suite = Suite(
        cases=[
            Case(
                id="calib",
                question="how big is the holdout",
                expect_tools=["get_calibration"],
                numeric=Truth(tool="get_calibration", path="holdout"),
            ),
            Case(id="stake", question="how much should i stake", refuse=True),
        ]
    )
    report = evals.run(Agent(client, box, strict=False), box, suite, "run-1")
    assert report.passed
    assert report.scores["tool_selection"] == 1.0
    assert report.scores["ungrounded_figures"] == 0.0
    assert report.counts["cases"] == 2


def test_the_report_survives_a_round_trip_through_json(box):
    client = FakeBedrock(said("We do not have that."))
    suite = Suite(cases=[Case(id="x", question="q", refuse=True)])
    report = evals.run(Agent(client, box), box, suite, "run-1")
    assert evals.Report.model_validate(json.loads(report.model_dump_json())).passed


def test_the_summary_names_the_cases_that_failed(box):
    client = FakeBedrock(said("It is about 88.4 percent."))
    suite = Suite(cases=[Case(id="stake", question="how much should i stake", refuse=True)])
    report = evals.run(Agent(client, box, strict=False), box, suite, "run-1")
    text = evals.summarise(report)
    assert "stake" in text
    assert "did not pass the gate" in text


def test_a_case_the_model_could_not_answer_does_not_lose_the_rest(box):
    class Angry:
        model_id = "test"

        def __init__(self):
            self.asked = 0

        def ask(self, question):
            self.asked += 1
            if self.asked == 1:
                raise RuntimeError("ThrottlingException: too many requests")
            return answer("We do not have that.")

    suite = Suite(
        cases=[
            Case(id="first", question="q", refuse=True),
            Case(id="second", question="q", refuse=True),
        ]
    )
    report = evals.run(Angry(), box, suite, "run-1", pace_seconds=0.0)
    assert [item.id for item in report.cases] == ["first", "second"]
    assert "ThrottlingException" in report.cases[0].detail
    assert report.cases[1].refusal_ok


def test_the_run_paces_itself_between_cases(box):
    waits = []

    class Quiet:
        model_id = "test"

        def ask(self, question):
            return answer("We do not have that.")

    suite = Suite(cases=[Case(id=f"c{n}", question="q", refuse=True) for n in range(3)])
    evals.run(Quiet(), box, suite, "run-1", pace_seconds=1.5, sleep=waits.append)
    assert waits == [1.5, 1.5]


def test_a_negative_ground_truth_matches_the_sign_the_model_wrote(box):
    truth = Truth(tool="get_driver_form", arguments={"driver_code": "AAA"}, path="form.value")
    case = Case(id="c", question="q", numeric=truth)
    value = evals.expected_value(box, truth)
    assert value < 0
    assert evals.score(case, answer(f"Rated **{value}**."), box).numeric_ok


@pytest.mark.parametrize(
    "text",
    [
        "I don't advise on betting.",
        "I can't do that.",
        "That is outside what this system does.",
        "The regulations are not available in the corpus.",
        "I would need historical results for that circuit.",
    ],
)
def test_the_ways_a_model_actually_declines_are_all_recognised(box, text):
    case = Case(id="c", question="q", refuse=True)
    assert evals.score(case, answer(text), box).refusal_ok
