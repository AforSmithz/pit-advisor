from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from botocore.exceptions import ClientError
from pydantic import BaseModel

# a question costs roughly six thousand input tokens, so twenty a day is a few cents a day and
# a bounded worst case. the page shows the number, because a limit nobody can see reads as a bug
DAILY_LIMIT: Final = 20
KEEP_FOR: Final = timedelta(days=2)


class Allowance(BaseModel, frozen=True):
    granted: bool
    used: int
    limit: int
    resets_at: datetime

    @property
    def left(self) -> int:
        return max(self.limit - self.used, 0)


class Budget(Protocol):
    def spend(self) -> Allowance: ...


def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _midnight_after(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


class DynamoBudget:
    """One counter row per UTC day, incremented under a condition. The condition is the whole
    point: two concurrent questions cannot both read nineteen and both be granted."""

    def __init__(
        self,
        table: str,
        client: Any,
        limit: int = DAILY_LIMIT,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.table = table
        self.client = client
        self.limit = limit
        self.clock = clock

    def spend(self) -> Allowance:
        now = self.clock()
        key = {"pk": {"S": f"ask#{_day(now)}"}}
        try:
            updated: Any = self.client.update_item(
                TableName=self.table,
                Key=key,
                UpdateExpression="ADD #asked :one SET expires_at = :ttl",
                ConditionExpression="attribute_not_exists(#asked) OR #asked < :limit",
                ExpressionAttributeNames={"#asked": "asked"},
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":limit": {"N": str(self.limit)},
                    ":ttl": {"N": str(int((now + KEEP_FOR).timestamp()))},
                },
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            return Allowance(
                granted=False, used=self.limit, limit=self.limit, resets_at=_midnight_after(now)
            )
        used = int(updated.get("Attributes", {}).get("asked", {}).get("N", "1"))
        return Allowance(granted=True, used=used, limit=self.limit, resets_at=_midnight_after(now))


class OpenBudget:
    """No cap. What a laptop and the eval runner get, where the spender is the one paying."""

    def __init__(self, limit: int = DAILY_LIMIT) -> None:
        self.limit = limit

    def spend(self) -> Allowance:
        now = datetime.now(UTC)
        return Allowance(granted=True, used=0, limit=self.limit, resets_at=_midnight_after(now))


def retry_after(allowance: Allowance, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    return max(int((allowance.resets_at - moment).total_seconds()), 1)
