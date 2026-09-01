import hashlib
import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from botocore.exceptions import ClientError
from pydantic import BaseModel

JOLPICA_HOURLY_CAP = 200
# open-meteo publishes a daily free-tier budget rather than an hourly one, this is well under it
OPEN_METEO_HOURLY_CAP = 600
# wikipedia asks for a descriptive user agent and courtesy rather than a published number
WIKIPEDIA_HOURLY_CAP = 500
# fia.com asks for ten seconds between requests in robots.txt, which is 360 an hour. the event
# document set is 1658 pdfs, so a third of the allowed rate would cost thirteen hours instead
# of five and buy nothing the site asked for
FIA_HOURLY_CAP = 360
HOURLY_CAPS: dict[str, int] = {
    "jolpica": JOLPICA_HOURLY_CAP,
    "open_meteo": OPEN_METEO_HOURLY_CAP,
    "wikipedia": WIKIPEDIA_HOURLY_CAP,
    "fia_docs": FIA_HOURLY_CAP,
}
LEDGER_TTL = timedelta(days=30)
MAX_CONTENTION_RETRIES = 6


class LedgerEntry(BaseModel, frozen=True):
    url: str
    fetched_at: datetime
    status: int
    etag: str | None = None
    last_modified: str | None = None


class Lease(BaseModel, frozen=True):
    granted: bool
    tokens_left: float
    retry_after: float = 0.0


class BucketState(BaseModel, frozen=True):
    name: str
    capacity: int
    tokens: float
    refill_per_second: float
    updated_at: datetime


class Ledger(Protocol):
    def lookup(self, url: str) -> LedgerEntry | None: ...

    def record(self, entry: LedgerEntry) -> None: ...


class Bucket(Protocol):
    def take(self, tokens: int = 1) -> Lease: ...

    def state(self) -> BucketState: ...


def url_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _refilled(tokens: float, capacity: int, rate: float, elapsed: float) -> float:
    return min(float(capacity), tokens + max(0.0, elapsed) * rate)


class LocalLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        return cast(dict[str, Any], json.loads(self.path.read_text()))

    def lookup(self, url: str) -> LedgerEntry | None:
        found = self._load().get(url_id(url))
        return LedgerEntry.model_validate(found) if found else None

    def record(self, entry: LedgerEntry) -> None:
        data = self._load()
        data[url_id(entry.url)] = entry.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))


class LocalBucket:
    def __init__(
        self,
        path: Path,
        name: str = "jolpica",
        capacity: int = JOLPICA_HOURLY_CAP,
        refill_per_second: float = JOLPICA_HOURLY_CAP / 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.name = name
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock

    def _read(self) -> tuple[float, float]:
        now = self.clock()
        if not self.path.is_file():
            return float(self.capacity), now
        stored: dict[str, Any] = json.loads(self.path.read_text()).get(self.name, {})
        if not stored:
            return float(self.capacity), now
        return float(stored["tokens"]), float(stored["updated_at"])

    def _write(self, tokens: float, now: float) -> None:
        data: dict[str, Any] = {}
        if self.path.is_file():
            data = json.loads(self.path.read_text())
        data[self.name] = {"tokens": tokens, "updated_at": now}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def take(self, tokens: int = 1) -> Lease:
        stored, updated_at = self._read()
        now = self.clock()
        available = _refilled(stored, self.capacity, self.refill_per_second, now - updated_at)
        if available < tokens:
            missing = tokens - available
            return Lease(
                granted=False,
                tokens_left=available,
                retry_after=missing / self.refill_per_second,
            )
        self._write(available - tokens, now)
        return Lease(granted=True, tokens_left=available - tokens)

    def state(self) -> BucketState:
        stored, updated_at = self._read()
        now = self.clock()
        return BucketState(
            name=self.name,
            capacity=self.capacity,
            tokens=_refilled(stored, self.capacity, self.refill_per_second, now - updated_at),
            refill_per_second=self.refill_per_second,
            updated_at=datetime.fromtimestamp(now, UTC),
        )


class DynamoLedger:
    def __init__(self, table: str, client: Any, ttl: timedelta = LEDGER_TTL) -> None:
        self.table = table
        self.client = client
        self.ttl = ttl

    def lookup(self, url: str) -> LedgerEntry | None:
        item: Any = self.client.get_item(
            TableName=self.table,
            Key={"pk": {"S": f"url#{url_id(url)}"}},
            ConsistentRead=True,
        ).get("Item")
        if not item:
            return None
        return LedgerEntry(
            url=item["url"]["S"],
            fetched_at=datetime.fromisoformat(item["fetched_at"]["S"]),
            status=int(item["status"]["N"]),
            etag=item.get("etag", {}).get("S"),
            last_modified=item.get("last_modified", {}).get("S"),
        )

    def record(self, entry: LedgerEntry) -> None:
        item: dict[str, Any] = {
            "pk": {"S": f"url#{url_id(entry.url)}"},
            "url": {"S": entry.url},
            "fetched_at": {"S": entry.fetched_at.isoformat()},
            "status": {"N": str(entry.status)},
            "expires_at": {"N": str(int((entry.fetched_at + self.ttl).timestamp()))},
        }
        if entry.etag:
            item["etag"] = {"S": entry.etag}
        if entry.last_modified:
            item["last_modified"] = {"S": entry.last_modified}
        self.client.put_item(TableName=self.table, Item=item)


class DynamoBucket:
    def __init__(
        self,
        table: str,
        client: Any,
        name: str = "jolpica",
        capacity: int = JOLPICA_HOURLY_CAP,
        refill_per_second: float = JOLPICA_HOURLY_CAP / 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.table = table
        self.client = client
        self.name = name
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock

    def _read(self) -> tuple[float, float]:
        item: Any = self.client.get_item(
            TableName=self.table,
            Key={"pk": {"S": f"quota#{self.name}"}},
            ConsistentRead=True,
        ).get("Item")
        if not item:
            return float(self.capacity), self.clock()
        return float(item["tokens"]["N"]), float(item["updated_at"]["N"])

    def take(self, tokens: int = 1) -> Lease:
        for _ in range(MAX_CONTENTION_RETRIES):
            stored, updated_at = self._read()
            now = self.clock()
            available = _refilled(stored, self.capacity, self.refill_per_second, now - updated_at)
            if available < tokens:
                return Lease(
                    granted=False,
                    tokens_left=available,
                    retry_after=(tokens - available) / self.refill_per_second,
                )
            try:
                self.client.update_item(
                    TableName=self.table,
                    Key={"pk": {"S": f"quota#{self.name}"}},
                    UpdateExpression="SET #tokens = :tokens, #updated = :now, #capacity = :cap",
                    # the guard is the whole point: two lambdas refilling from the same read
                    # would each think the hour still had room
                    ConditionExpression=("attribute_not_exists(pk) OR #updated = :seen_at"),
                    # capacity and tokens are both reserved words in dynamodb
                    ExpressionAttributeNames={
                        "#tokens": "tokens",
                        "#updated": "updated_at",
                        "#capacity": "capacity",
                    },
                    ExpressionAttributeValues={
                        ":tokens": {"N": str(available - tokens)},
                        ":now": {"N": str(now)},
                        ":cap": {"N": str(self.capacity)},
                        ":seen_at": {"N": str(updated_at)},
                    },
                )
            except ClientError as exc:
                code: Any = cast(Any, exc).response.get("Error", {}).get("Code")
                if code != "ConditionalCheckFailedException":
                    raise
                continue
            return Lease(granted=True, tokens_left=available - tokens)
        return Lease(granted=False, tokens_left=0.0, retry_after=1.0)

    def state(self) -> BucketState:
        stored, updated_at = self._read()
        now = self.clock()
        return BucketState(
            name=self.name,
            capacity=self.capacity,
            tokens=_refilled(stored, self.capacity, self.refill_per_second, now - updated_at),
            refill_per_second=self.refill_per_second,
            updated_at=datetime.fromtimestamp(now, UTC),
        )


class QuotaExhaustedError(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"quota exhausted, {retry_after:.0f}s until the next token")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(
        self,
        bucket: Bucket,
        max_wait: float = 90.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.bucket = bucket
        self.max_wait = max_wait
        self.sleep = sleep
        self.jitter = jitter

    def acquire(self, tokens: int = 1) -> Lease:
        lease = self.bucket.take(tokens)
        if lease.granted:
            return lease
        if lease.retry_after > self.max_wait:
            raise QuotaExhaustedError(lease.retry_after)
        self.sleep(lease.retry_after + self.jitter())
        lease = self.bucket.take(tokens)
        if not lease.granted:
            raise QuotaExhaustedError(lease.retry_after)
        return lease

    def backoff(self, attempt: int, base: float = 2.0) -> None:
        self.sleep(min(60.0, base**attempt) + self.jitter())
