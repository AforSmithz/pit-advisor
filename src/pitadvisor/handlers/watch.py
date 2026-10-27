import json
from datetime import UTC, datetime
from typing import Any, cast

from aws_lambda_powertools import Logger

from pitadvisor.config import Settings, boto_session, get_settings
from pitadvisor.ingest import watch
from pitadvisor.ingest.ratelimit import HOURLY_CAPS, DynamoBucket, RateLimiter
from pitadvisor.ingest.raw_store import object_store
from pitadvisor.quality.checks import read_table
from pitadvisor.types import Layer

logger = Logger(service="pitadvisor-watch")


def _limiter(settings: Settings, dynamo: Any, name: str) -> RateLimiter:
    return RateLimiter(
        DynamoBucket(
            settings.ledger_table,
            dynamo,
            name=name,
            capacity=HOURLY_CAPS[name],
            refill_per_second=HOURLY_CAPS[name] / 3600,
        )
    )


def poll(settings: Settings) -> dict[str, Any]:
    session = cast(Any, boto_session(settings))
    store = object_store(settings)
    races = read_table(store, Layer.BRONZE, "races")
    if races is None:
        return {"watching": False}
    states = session.client("stepfunctions", region_name=settings.aws_region)
    dynamo = session.client("dynamodb", region_name=settings.aws_region)
    machines = [settings.weekend_machine_arn, settings.light_machine_arn]

    def running() -> bool:
        return any(
            states.list_executions(stateMachineArn=arn, statusFilter="RUNNING", maxResults=1)[
                "executions"
            ]
            for arn in machines
        )

    def start(action: watch.Action) -> None:
        name, payload = watch.execution(action, datetime.now(UTC))
        arn = settings.weekend_machine_arn if action.mode == "full" else settings.light_machine_arn
        states.start_execution(stateMachineArn=arn, name=name, input=json.dumps(payload))
        logger.info("started", execution=name, reason=action.reason)

    probes = watch.live_probes(
        store,
        _limiter(settings, dynamo, "openf1"),
        _limiter(settings, dynamo, "jolpica"),
        _limiter(settings, dynamo, "fia_docs"),
    )
    action = watch.poll(datetime.now(UTC), races.to_dicts(), store, probes, running, start)
    return {"action": action.model_dump(mode="json") if action else None}


def publish(settings: Settings) -> dict[str, Any]:
    session = cast(Any, boto_session(settings))
    ssm = session.client("ssm", region_name=settings.aws_region)
    try:
        token = ssm.get_parameter(Name=settings.dispatch_parameter, WithDecryption=True)
    except ssm.exceptions.ParameterNotFound:
        # no token yet means no automatic publish, which must not fail the run that got here
        logger.warning("no dispatch token", parameter=settings.dispatch_parameter)
        return {"published": False}
    status = watch.dispatch(settings.github_repo, str(token["Parameter"]["Value"]))
    logger.info("dispatched", status=status)
    return {"published": True, "status": status}


@logger.inject_lambda_context
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    settings = get_settings()
    if event.get("action") == "publish":
        return publish(settings)
    return poll(settings)
