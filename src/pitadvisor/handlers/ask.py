import json
from typing import Any, cast

from aws_lambda_powertools import Logger

from pitadvisor.agent.budget import Budget, DynamoBudget, retry_after
from pitadvisor.agent.runtime import agent_for
from pitadvisor.agent.tools import toolbox
from pitadvisor.config import Settings, boto_session, get_settings
from pitadvisor.ingest.raw_store import object_store

logger = Logger(service="pitadvisor-ask")

MAX_QUESTION = 500


def budget_for(settings: Settings) -> Budget:
    session = cast(Any, boto_session(settings))
    return DynamoBudget(
        settings.ledger_table,
        session.client("dynamodb", region_name=settings.aws_region),
        limit=settings.ask_daily_limit,
    )


@logger.inject_lambda_context
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    settings = get_settings()
    body = json.loads(event.get("body") or "{}") if "body" in event else event
    question = str(body.get("question", "")).strip()
    if not question:
        return _reply(400, {"error": "ask for something"})
    if len(question) > MAX_QUESTION:
        return _reply(400, {"error": f"keep it under {MAX_QUESTION} characters"})

    # the cap is taken before the model is called, not after, so a refusal costs nothing
    allowance = budget_for(settings).spend()
    if not allowance.granted:
        logger.info("budget spent", limit=allowance.limit)
        return _reply(
            429,
            {
                "error": (
                    f"this demo answers {allowance.limit} questions a day and today's are gone. "
                    "it runs on a fixed credit budget, so the cap is the point"
                ),
                "limit": allowance.limit,
                "resets_at": allowance.resets_at.isoformat(),
            },
            headers={"retry-after": str(retry_after(allowance))},
        )

    answer = agent_for(settings, toolbox(settings, object_store(settings))).ask(question)
    logger.info(
        "answered",
        tools=answer.tools_used,
        grounded=answer.grounded,
        tokens=answer.usage.get("totalTokens", 0),
        asked_today=allowance.used,
    )
    payload = json.loads(answer.model_dump_json())
    payload["budget"] = {"used": allowance.used, "limit": allowance.limit}
    return _reply(200, payload)


def _reply(
    status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", **(headers or {})},
        "body": json.dumps(payload),
    }
