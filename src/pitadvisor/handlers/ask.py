import json
from typing import Any

from aws_lambda_powertools import Logger

from pitadvisor.agent.runtime import agent_for
from pitadvisor.agent.tools import toolbox
from pitadvisor.config import get_settings
from pitadvisor.ingest.raw_store import object_store

logger = Logger(service="pitadvisor-ask")


@logger.inject_lambda_context
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    settings = get_settings()
    body = json.loads(event.get("body") or "{}") if "body" in event else event
    question = str(body.get("question", "")).strip()
    if not question:
        return _reply(400, {"error": "ask for something"})
    answer = agent_for(settings, toolbox(settings, object_store(settings))).ask(question)
    logger.info(
        "answered",
        tools=answer.tools_used,
        grounded=answer.grounded,
        tokens=answer.usage.get("totalTokens", 0),
    )
    return _reply(200, json.loads(answer.model_dump_json()))


def _reply(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }
