from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from pitadvisor.ingest.ratelimit import (
    DynamoBucket,
    DynamoLedger,
    LedgerEntry,
    LocalBucket,
    QuotaExhaustedError,
    RateLimiter,
    url_id,
)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def entry(url="https://api.jolpi.ca/x", status=200):
    return LedgerEntry(url=url, fetched_at=datetime.now(UTC), status=status, etag='"abc"')


def test_local_ledger_round_trip(local_ledger):
    local_ledger.record(entry())
    found = local_ledger.lookup("https://api.jolpi.ca/x")
    assert found.etag == '"abc"'
    assert found.status == 200


def test_local_ledger_misses_are_none(local_ledger):
    assert local_ledger.lookup("https://api.jolpi.ca/never") is None


def test_local_ledger_keeps_the_last_write(local_ledger):
    local_ledger.record(entry())
    local_ledger.record(entry(status=304))
    assert local_ledger.lookup("https://api.jolpi.ca/x").status == 304


def test_url_id_is_stable_and_distinct():
    assert url_id("https://a") == url_id("https://a")
    assert url_id("https://a") != url_id("https://b")


def test_bucket_starts_full(tmp_path):
    bucket = LocalBucket(tmp_path / "q.json", capacity=5, refill_per_second=1.0)
    assert bucket.state().tokens == 5


def test_bucket_spends_a_token(tmp_path):
    bucket = LocalBucket(tmp_path / "q.json", capacity=5, refill_per_second=1.0)
    lease = bucket.take()
    assert lease.granted
    assert lease.tokens_left == 4


def test_bucket_refuses_when_empty_and_says_when_to_retry(tmp_path):
    clock = Clock()
    bucket = LocalBucket(tmp_path / "q.json", capacity=2, refill_per_second=0.5, clock=clock)
    bucket.take(2)
    lease = bucket.take()
    assert not lease.granted
    assert lease.retry_after == pytest.approx(2.0)


def test_bucket_refills_over_time(tmp_path):
    clock = Clock()
    bucket = LocalBucket(tmp_path / "q.json", capacity=10, refill_per_second=1.0, clock=clock)
    bucket.take(10)
    clock.advance(4)
    assert bucket.state().tokens == pytest.approx(4)
    assert bucket.take(4).granted


def test_bucket_never_refills_past_capacity(tmp_path):
    clock = Clock()
    bucket = LocalBucket(tmp_path / "q.json", capacity=3, refill_per_second=1.0, clock=clock)
    bucket.take(1)
    clock.advance(10_000)
    assert bucket.state().tokens == 3


def test_bucket_survives_a_new_process(tmp_path):
    path = tmp_path / "q.json"
    LocalBucket(path, capacity=5, refill_per_second=0.0).take(3)
    assert LocalBucket(path, capacity=5, refill_per_second=0.0).state().tokens == 2


class FakeDynamo:
    def __init__(self, item=None, fail_conditions=0):
        self.item = item
        self.fail_conditions = fail_conditions
        self.updates = []
        self.puts = []

    def get_item(self, TableName, Key, ConsistentRead=False):
        return {"Item": self.item} if self.item else {}

    def update_item(self, **kwargs):
        if self.fail_conditions > 0:
            self.fail_conditions -= 1
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        self.updates.append(kwargs)
        values = kwargs["ExpressionAttributeValues"]
        self.item = {
            "tokens": {"N": values[":tokens"]["N"]},
            "updated_at": {"N": values[":now"]["N"]},
        }
        return {}

    def put_item(self, TableName, Item):
        self.puts.append(Item)


def test_dynamo_bucket_starts_full_when_the_item_is_missing():
    bucket = DynamoBucket("t", FakeDynamo(), capacity=200, refill_per_second=1.0)
    assert bucket.state().tokens == 200


def test_dynamo_bucket_writes_the_remaining_tokens():
    client = FakeDynamo()
    bucket = DynamoBucket("t", client, capacity=10, refill_per_second=0.0, clock=Clock())
    assert bucket.take(3).granted
    assert client.updates[0]["ExpressionAttributeValues"][":tokens"]["N"] == "7.0"


def test_dynamo_bucket_aliases_the_reserved_attribute_names():
    client = FakeDynamo()
    DynamoBucket("t", client, capacity=10, refill_per_second=0.0, clock=Clock()).take()
    names = client.updates[0]["ExpressionAttributeNames"]
    assert set(names.values()) == {"tokens", "updated_at", "capacity"}
    expression = client.updates[0]["UpdateExpression"]
    assert "capacity" not in expression.replace("#capacity", "")
    assert "tokens" not in expression.replace("#tokens", "").replace(":tokens", "")


def test_dynamo_bucket_guards_against_a_concurrent_writer():
    client = FakeDynamo(fail_conditions=2)
    bucket = DynamoBucket("t", client, capacity=10, refill_per_second=0.0, clock=Clock())
    assert bucket.take().granted
    assert len(client.updates) == 1


def test_dynamo_bucket_refuses_when_the_hour_is_spent():
    clock = Clock()
    client = FakeDynamo(item={"tokens": {"N": "0"}, "updated_at": {"N": str(clock.now)}})
    bucket = DynamoBucket("t", client, capacity=200, refill_per_second=0.05, clock=clock)
    lease = bucket.take()
    assert not lease.granted
    assert lease.retry_after == pytest.approx(20.0)


def test_dynamo_bucket_reraises_an_unrelated_client_error():
    class Broken(FakeDynamo):
        def update_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceeded"}}, "UpdateItem")

    with pytest.raises(ClientError):
        DynamoBucket("t", Broken(), refill_per_second=0.0).take()


def test_dynamo_ledger_records_an_expiry():
    client = FakeDynamo()
    DynamoLedger("t", client).record(entry())
    item = client.puts[0]
    assert item["pk"]["S"].startswith("url#")
    assert int(item["expires_at"]["N"]) > 0
    assert item["etag"]["S"] == '"abc"'


def test_dynamo_ledger_reads_an_item_back():
    stamp = datetime(2024, 5, 5, 12, tzinfo=UTC)
    client = FakeDynamo(
        item={
            "url": {"S": "https://api.jolpi.ca/x"},
            "fetched_at": {"S": stamp.isoformat()},
            "status": {"N": "304"},
            "etag": {"S": '"abc"'},
        }
    )
    found = DynamoLedger("t", client).lookup("https://api.jolpi.ca/x")
    assert found.status == 304
    assert found.fetched_at == stamp


def test_dynamo_ledger_miss_is_none():
    assert DynamoLedger("t", FakeDynamo()).lookup("https://api.jolpi.ca/x") is None


def test_limiter_waits_once_then_takes(tmp_path):
    clock = Clock()
    slept = []
    bucket = LocalBucket(tmp_path / "q.json", capacity=1, refill_per_second=1.0, clock=clock)
    bucket.take()

    def sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    limiter = RateLimiter(bucket, sleep=sleep, jitter=lambda: 0.0)
    assert limiter.acquire().granted
    assert slept == [pytest.approx(1.0)]


def test_limiter_gives_up_past_max_wait(tmp_path):
    bucket = LocalBucket(tmp_path / "q.json", capacity=1, refill_per_second=0.001)
    bucket.take()
    limiter = RateLimiter(bucket, max_wait=5, sleep=lambda _: None, jitter=lambda: 0.0)
    with pytest.raises(QuotaExhaustedError) as raised:
        limiter.acquire()
    assert raised.value.retry_after > 5


def test_backoff_is_bounded():
    slept = []
    limiter = RateLimiter(None, sleep=slept.append, jitter=lambda: 0.0)
    limiter.backoff(0)
    limiter.backoff(12)
    assert slept == [1.0, 60.0]
