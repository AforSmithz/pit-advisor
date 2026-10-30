import io
import json
import urllib.error
from email.message import Message

import pytest

from pitadvisor.agent import tools
from pitadvisor.agent.chat import ChatCompletions, ChatError, from_chat, to_chat
from pitadvisor.agent.runtime import Agent, agent_for
from pitadvisor.agent.tools import Toolbox
from pitadvisor.config import Settings


def completion(content=None, calls=(), finish="stop", usage=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in calls
        ]
    return {
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage or {"prompt_tokens": 30, "completion_tokens": 6, "total_tokens": 36},
    }


class Endpoint:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []
        self.headers = []

    def __call__(self, request, timeout):
        self.bodies.append(json.loads(request.data))
        self.headers.append(dict(request.header_items()))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def http_error(code, headers=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://x", code, "nope", message, io.BytesIO(b"slow down"))


@pytest.fixture
def box(views):
    return Toolbox(store=views)


@pytest.fixture
def endpoint(monkeypatch):
    def install(*replies):
        fake = Endpoint(*replies)
        monkeypatch.setattr("urllib.request.urlopen", fake)
        return fake

    return install


def client(**kwargs):
    return ChatCompletions("https://foundry.test/openai/v1/", "k", sleep=lambda _: None, **kwargs)


def test_the_converse_request_becomes_a_chat_request():
    body = to_chat(
        {
            "modelId": "gpt-oss-120b",
            "system": [{"text": "be grounded"}, {"cachePoint": {"type": "default"}}],
            "messages": [{"role": "user", "content": [{"text": "how is AAA going"}]}],
            "toolConfig": {"tools": [*tools.specs(), {"cachePoint": {"type": "default"}}]},
            "inferenceConfig": {"maxTokens": 1024, "temperature": 0.0},
        },
        reasoning_effort="none",
    )
    assert body["model"] == "gpt-oss-120b"
    assert body["messages"] == [
        {"role": "system", "content": "be grounded"},
        {"role": "user", "content": "how is AAA going"},
    ]
    assert len(body["tools"]) == len(tools.TOOLS)
    first = body["tools"][0]["function"]
    assert first["parameters"] == tools.specs()[0]["toolSpec"]["inputSchema"]["json"]
    assert body["max_completion_tokens"] == 1024
    assert body["temperature"] == 0.0
    assert body["reasoning_effort"] == "none"


def test_no_reasoning_effort_is_sent_unless_asked_for():
    body = to_chat({"modelId": "m", "messages": [{"role": "user", "content": [{"text": "q"}]}]})
    assert "reasoning_effort" not in body
    assert "tools" not in body
    assert "tool_choice" not in body


@pytest.mark.parametrize(
    ("converse", "chat"),
    [
        ({"tool": {"name": "record"}}, {"type": "function", "function": {"name": "record"}}),
        ({"any": {}}, "required"),
        ({"auto": {}}, "auto"),
    ],
)
def test_a_tool_choice_carries_over(converse, chat):
    body = to_chat(
        {
            "modelId": "m",
            "messages": [{"role": "user", "content": [{"text": "q"}]}],
            "toolConfig": {"tools": tools.specs(), "toolChoice": converse},
        }
    )
    assert body["tool_choice"] == chat


def test_extraction_runs_through_the_chat_endpoint(endpoint):
    from pitadvisor.incidents.extract import TOOL, extract

    raw = (
        "Decision\nNo / Driver: 4 - Lando Norris\nCompetitor: McLaren\n"
        "Fact: Causing a collision with Car 1\nOutcome: 5 second time penalty\n"
    )
    fake = endpoint(
        completion(
            calls=[
                (
                    "c1",
                    TOOL,
                    json.dumps({"entries": [{"car": 4, "outcome": "5 second time penalty"}]}),
                )
            ],
            finish="tool_calls",
        )
    )
    result = extract(client(), "gpt-6-luna", raw)
    assert fake.bodies[0]["tool_choice"] == {"type": "function", "function": {"name": TOOL}}
    assert result.decisions[0].outcome == "5 second time penalty"
    assert result.input_tokens == 30


def test_a_tool_call_finished_as_stop_still_reads_as_tool_use():
    response = from_chat(
        completion(calls=[("c1", "get_driver_form", '{"driver_code": "AAA"}')], finish="stop")
    )
    assert response["stopReason"] == "tool_use"
    use = response["output"]["message"]["content"][0]["toolUse"]
    assert use == {"toolUseId": "c1", "name": "get_driver_form", "input": {"driver_code": "AAA"}}


def test_usage_maps_onto_the_bedrock_names():
    response = from_chat(
        completion(
            "fine",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 64},
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        )
    )
    assert response["usage"] == {
        "inputTokens": 100,
        "outputTokens": 20,
        "totalTokens": 120,
        "cacheReadInputTokens": 64,
        "reasoningTokens": 12,
    }
    assert response["stopReason"] == "end_turn"


def test_an_empty_response_is_an_error_not_a_silent_blank():
    with pytest.raises(ChatError):
        from_chat({"choices": []})


def test_the_agent_loop_runs_a_tool_through_the_chat_endpoint(box, endpoint):
    fake = endpoint(
        completion(
            calls=[("c1", "get_driver_form", '{"driver_code": "AAA"}')], finish="tool_calls"
        ),
        completion("AAA is rated, with the interval reported alongside."),
    )
    answer = Agent(client(), box, model_id="gpt-oss-120b").ask("how is AAA going")
    assert answer.tools_used == ["get_driver_form"]
    assert answer.calls[0].ok
    assert answer.stop_reason == "end_turn"
    assert answer.usage["inputTokens"] == 60
    replay = fake.bodies[1]["messages"]
    assert replay[2]["role"] == "assistant"
    assert replay[2]["tool_calls"][0]["id"] == "c1"
    assert json.loads(replay[2]["tool_calls"][0]["function"]["arguments"]) == {"driver_code": "AAA"}
    assert replay[3]["role"] == "tool"
    assert replay[3]["tool_call_id"] == "c1"
    assert json.loads(replay[3]["content"])["tool"] == "get_driver_form"
    assert fake.headers[0]["Api-key"] == "k"


def test_arguments_that_are_not_json_fail_the_tool_rather_than_run_on_defaults(box, endpoint):
    endpoint(
        completion(calls=[("c1", "get_driver_form", "{driver_code: AAA")], finish="tool_calls"),
        completion("I could not read that."),
    )
    answer = Agent(client(), box, model_id="m").ask("how is AAA going")
    assert not answer.calls[0].ok
    assert "bad arguments" in answer.calls[0].detail


def test_a_429_waits_as_long_as_azure_says_then_retries(endpoint):
    waits = []
    endpoint(http_error(429, {"retry-after-ms": "1500"}), completion("ok"))
    chat = ChatCompletions("https://foundry.test/openai/v1", "k", sleep=waits.append)
    response = chat.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])
    assert waits == [1.5]
    assert response["output"]["message"]["content"] == [{"text": "ok"}]


def test_a_bad_request_is_not_retried(endpoint):
    fake = endpoint(http_error(400), completion("never reached"))
    with pytest.raises(ChatError, match="400"):
        client().converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])
    assert len(fake.bodies) == 1


def test_retries_run_out(endpoint):
    endpoint(*[http_error(503)] * 3)
    with pytest.raises(ChatError, match="503"):
        client(attempts=3).converse(
            modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}]
        )


def test_the_openai_provider_needs_its_endpoint_settings(box):
    settings = Settings(model_provider="openai", openai_base_url="https://x", _env_file=None)
    with pytest.raises(ValueError, match="openai_api_key, openai_model"):
        agent_for(settings, box)


def test_the_openai_provider_skips_bedrock_and_its_guardrail(box):
    settings = Settings(
        model_provider="openai",
        openai_base_url="https://x/openai/v1",
        openai_api_key="k",
        openai_model="gpt-6-luna",
        openai_reasoning_effort="none",
        guardrail_id="g",
        _env_file=None,
    )
    agent = agent_for(settings, box)
    assert isinstance(agent.client, ChatCompletions)
    assert agent.client.reasoning_effort == "none"
    assert agent.model_id == "gpt-6-luna"
    assert agent.guardrail is None


def test_an_unknown_provider_is_refused(box):
    with pytest.raises(ValueError, match="unknown"):
        agent_for(Settings(model_provider="vertex", _env_file=None), box)
