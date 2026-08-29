from typing import Any

from aws_lambda_powertools import Logger

from pitadvisor.agent.tools import MAX_SIM_PATHS, RaceSim
from pitadvisor.config import get_settings
from pitadvisor.ingest.raw_store import object_store

logger = Logger(service="pitadvisor-race-sim")


@logger.inject_lambda_context
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    settings = get_settings()
    paths = min(int(event.get("paths", 1000)), MAX_SIM_PATHS)
    result = RaceSim(object_store(settings)).simulate(
        str(event.get("event", "next")), str(event.get("scenario", "dry")), paths
    )
    logger.info("simulated", scenario=result["scenario"], paths=result["paths"])
    return result
