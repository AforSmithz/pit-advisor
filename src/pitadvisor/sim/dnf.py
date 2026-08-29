from datetime import date

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from pitadvisor.features.reliability import Cause, ReliabilityFit

# a hazard of exactly zero says a car cannot break, which no team has managed
FLOOR_PER_LAP = 1e-5


class RetirementModel(BaseModel, frozen=True):
    """The car carries the failures and the driver carries the incidents, so a seat's hazard
    is its team's pooled rate moved by how much more or less this driver crashes than the
    field. Adding the two outright would count every collision twice."""

    as_of: date
    per_lap: dict[str, float]
    field_per_lap: float
    field_collision_per_lap: float
    cause_coverage: float


def build(
    fitted: ReliabilityFit,
    seats: dict[str, str],
    driver_ids: dict[str, str],
) -> RetirementModel:
    teams = {hazard.key: hazard.per_lap for hazard in fitted.teams if hazard.cause is Cause.ANY}
    incidents = {
        hazard.key: hazard.per_lap for hazard in fitted.drivers if hazard.cause is Cause.COLLISION
    }
    field = fitted.field_rates.get(str(Cause.ANY), FLOOR_PER_LAP)
    field_collision = fitted.field_rates.get(str(Cause.COLLISION), 0.0)
    per_lap: dict[str, float] = {}
    for code, team in seats.items():
        base = teams.get(team, field)
        own = incidents.get(driver_ids.get(code, ""), field_collision)
        per_lap[code] = max(base + own - field_collision, FLOOR_PER_LAP)
    return RetirementModel(
        as_of=fitted.as_of,
        per_lap=per_lap,
        field_per_lap=field,
        field_collision_per_lap=field_collision,
        cause_coverage=fitted.cause_coverage,
    )


def sample(
    model: RetirementModel,
    codes: list[str],
    laps: int,
    paths: int,
    rng: np.random.Generator,
) -> npt.NDArray[np.int64]:
    """(paths, drivers) of the lap a car stopped on, or laps + 1 for one that finished."""
    hazard = np.asarray([model.per_lap.get(code, model.field_per_lap) for code in codes])
    struck = rng.random((paths, len(codes), laps)) < hazard.reshape(1, -1, 1)
    ever = struck.any(axis=2)
    first = struck.argmax(axis=2) + 1
    return np.asarray(np.where(ever, first, laps + 1), dtype=np.int64)
