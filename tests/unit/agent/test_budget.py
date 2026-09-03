from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from pitadvisor.agent.budget import DynamoBudget, OpenBudget, retry_after

NOON = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)


class FakeDynamo:
    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.rows: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    def update_item(self, **request: Any) -> dict[str, Any]:
        self.calls.append(request)
        key = request["Key"]["pk"]["S"]
        used = self.rows.get(key, 0)
        if used >= self.limit:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}},
                "UpdateItem",
            )
        self.rows[key] = used + 1
        return {"Attributes": {"asked": {"N": str(self.rows[key])}}}


def budget(client: FakeDynamo, limit: int = 3) -> DynamoBudget:
    return DynamoBudget("ledger", client, limit=limit, clock=lambda: NOON)


def test_each_question_counts_against_the_day():
    client = FakeDynamo()
    used = [budget(client).spend().used for _ in range(3)]
    assert used == [1, 2, 3]
    assert client.rows == {"ask#2026-09-03": 3}


def test_the_day_after_the_limit_is_refused_not_charged():
    client = FakeDynamo()
    for _ in range(3):
        budget(client).spend()
    denied = budget(client).spend()
    assert not denied.granted
    assert denied.left == 0
    assert denied.resets_at == datetime(2026, 9, 4, tzinfo=UTC)


def test_the_count_is_incremented_under_a_condition_not_read_then_written():
    # two questions arriving together must not both read nineteen and both be granted
    client = FakeDynamo()
    budget(client).spend()
    request = client.calls[0]
    assert "ConditionExpression" in request
    assert request["UpdateExpression"].startswith("ADD ")
    assert request["ExpressionAttributeValues"][":limit"] == {"N": "3"}


def test_the_row_expires_so_yesterdays_counter_does_not_linger():
    client = FakeDynamo()
    budget(client).spend()
    ttl = int(client.calls[0]["ExpressionAttributeValues"][":ttl"]["N"])
    assert ttl > NOON.timestamp()


def test_an_error_that_is_not_the_limit_is_not_swallowed():
    class Broken(FakeDynamo):
        def update_item(self, **request: Any) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    with pytest.raises(ClientError):
        budget(Broken()).spend()


def test_retry_after_is_the_seconds_until_the_day_turns():
    client = FakeDynamo()
    for _ in range(3):
        budget(client).spend()
    denied = budget(client).spend()
    assert retry_after(denied, NOON) == 11 * 3600 + 30 * 60


def test_the_laptop_pays_for_itself_and_is_not_capped():
    allowance = OpenBudget().spend()
    assert allowance.granted
    assert allowance.used == 0
