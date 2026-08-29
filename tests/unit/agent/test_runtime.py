import pytest

from pitadvisor.agent import tools
from pitadvisor.agent.runtime import Agent, ToolCall, ungrounded
from pitadvisor.agent.tools import Toolbox


class FakeBedrock:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return self.turns.pop(0)


def said(text, stop="end_turn", usage=None):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
        "usage": usage or {"inputTokens": 10, "outputTokens": 4, "totalTokens": 14},
    }


def wants(name, arguments, use_id="tu-1"):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": use_id, "name": name, "input": arguments}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 20, "outputTokens": 8, "totalTokens": 28},
    }


@pytest.fixture
def box(views):
    return Toolbox(store=views)


def test_a_question_needing_no_tool_comes_straight_back(box):
    client = FakeBedrock(said("The forecast page is at /forecast."))
    answer = Agent(client, box).ask("where does the forecast live")
    assert answer.text.startswith("The forecast")
    assert answer.calls == []
    assert answer.stop_reason == "end_turn"


def test_a_tool_use_turn_runs_the_tool_and_feeds_the_result_back(box):
    client = FakeBedrock(
        wants("get_driver_form", {"driver_code": "AAA"}),
        said("AAA is rated, with the interval reported alongside."),
    )
    answer = Agent(client, box).ask("how is AAA going")
    assert answer.tools_used == ["get_driver_form"]
    assert answer.calls[0].ok
    fed = client.requests[1]["messages"][2]["content"][0]["toolResult"]
    assert fed["toolUseId"] == "tu-1"
    assert fed["status"] == "success"
    assert fed["content"][0]["json"]["payload"]["driver_code"] == "AAA"


def test_a_failing_tool_comes_back_as_an_error_result_not_an_exception(box):
    client = FakeBedrock(
        wants("get_driver_form", {"driver_code": "ZZZ"}),
        said("We do not have that driver."),
    )
    answer = Agent(client, box).ask("how is ZZZ going")
    assert not answer.calls[0].ok
    assert client.requests[1]["messages"][2]["content"][0]["toolResult"]["status"] == "error"


def test_a_tool_that_does_not_exist_is_reported_to_the_model(box):
    client = FakeBedrock(wants("get_lap_times", {}), said("No such tool."))
    answer = Agent(client, box).ask("lap times please")
    assert "no tool called" in answer.calls[0].detail


def test_a_figure_the_model_invented_is_withheld(box):
    client = FakeBedrock(said("AAA wins about 41.7% of the time."))
    answer = Agent(client, box).ask("what are AAA's chances")
    assert answer.refused
    assert answer.ungrounded == ["41.7"]
    assert "does not publish a number it cannot trace" in answer.text


def test_a_figure_that_came_from_a_tool_survives(box):
    result = tools.invoke(box, "get_forecast", {"market": "win"})
    win = result.payload["drivers"][0]["win"]
    client = FakeBedrock(
        wants("get_forecast", {"market": "win"}),
        said(f"The simulation gives {win * 100:.1f}% for the win."),
    )
    answer = Agent(client, box).ask("who wins")
    assert answer.grounded
    assert not answer.refused


def test_a_number_the_asker_supplied_is_not_an_invention(box):
    client = FakeBedrock(said("Round 6 is the one you mean."))
    assert Agent(client, box).ask("what happened at round 6").grounded


def test_a_season_in_the_answer_is_not_treated_as_a_figure(box):
    client = FakeBedrock(said("That rule changed for the 2022 season."))
    assert Agent(client, box).ask("when did ground effect come back").grounded


def test_the_loose_check_can_be_turned_off_without_losing_the_finding(box):
    client = FakeBedrock(said("Roughly 12.5 points a race."))
    answer = Agent(client, box, strict=False).ask("how many points")
    assert not answer.refused
    assert answer.ungrounded == ["12.5"]


def test_the_system_prompt_and_the_tools_both_carry_a_cache_point(box):
    client = FakeBedrock(said("ok"))
    Agent(client, box).ask("hello")
    request = client.requests[0]
    assert request["system"][-1] == {"cachePoint": {"type": "default"}}
    assert request["toolConfig"]["tools"][-1] == {"cachePoint": {"type": "default"}}


def test_max_tokens_is_always_set_explicitly(box):
    client = FakeBedrock(said("ok"))
    Agent(client, box).ask("hello")
    assert client.requests[0]["inferenceConfig"]["maxTokens"] > 0


def test_no_guardrail_is_sent_when_none_is_configured(box):
    client = FakeBedrock(said("ok"))
    Agent(client, box).ask("hello")
    assert "guardrailConfig" not in client.requests[0]


def test_a_configured_guardrail_travels_with_every_call(box):
    client = FakeBedrock(said("ok"))
    Agent(client, box, guardrail=("gr-1", "DRAFT")).ask("hello")
    assert client.requests[0]["guardrailConfig"]["guardrailIdentifier"] == "gr-1"


def test_the_loop_stops_at_the_iteration_cap(box):
    client = FakeBedrock(*[wants("get_calibration", {}, f"tu-{n}") for n in range(4)])
    answer = Agent(client, box, max_iterations=4).ask("go around forever")
    assert answer.iterations == 4
    assert answer.stop_reason == "tool_use"
    assert len(answer.calls) == 4


def test_token_usage_adds_up_across_turns(box):
    client = FakeBedrock(wants("get_calibration", {}), said("done"))
    answer = Agent(client, box).ask("how did it score")
    assert answer.usage["totalTokens"] == 28 + 14


def test_a_percentage_the_tool_gave_as_a_fraction_reads_either_way():
    call = ToolCall(name="get_forecast", arguments={}, ok=True)
    result = tools.ToolResult(tool="get_forecast", ok=True, payload={"win": 0.325})
    assert ungrounded("32.5% chance", "who wins", [call], [result]) == []
    assert ungrounded("0.325 chance", "who wins", [call], [result]) == []
    assert ungrounded("33.1% chance", "who wins", [call], [result]) == ["33.1"]
