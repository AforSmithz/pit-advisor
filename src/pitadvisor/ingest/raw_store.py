import io
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import polars as pl
from botocore.exceptions import ClientError
from pydantic import BaseModel

from pitadvisor.config import Settings, boto_session
from pitadvisor.types import (
    EventKey,
    Provenance,
    SeasonKey,
    SessionKey,
    Source,
    bronze_key,
    quarantine_key,
    raw_filename,
    raw_key,
)

META_SUFFIX = ".meta.json"


class RawOverwriteError(RuntimeError):
    pass


class StoredObject(BaseModel, frozen=True):
    key: str
    size: int
    modified_at: datetime


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str) -> Iterator[StoredObject]: ...


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, body: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return f"file://{path}"

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> Iterator[StoredObject]:
        base = self._path(prefix)
        root = base if base.is_dir() else base.parent
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if not key.startswith(prefix):
                continue
            stat = path.stat()
            yield StoredObject(
                key=key,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            )

    def clear(self, prefix: str) -> None:
        target = self._path(prefix)
        if target.is_dir():
            shutil.rmtree(target)


class S3ObjectStore:
    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self.client = client

    def put(self, key: str, body: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        body: Any = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        return cast(bytes, body.read())

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return False
        return True

    def list(self, prefix: str) -> Iterator[StoredObject]:
        pages: Any = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        )
        for page in pages:
            for item in page.get("Contents", []):
                yield StoredObject(
                    key=item["Key"],
                    size=int(item["Size"]),
                    modified_at=item["LastModified"].astimezone(UTC),
                )


def object_store(settings: Settings, local_root: Path | None = None) -> ObjectStore:
    if local_root is not None:
        return LocalObjectStore(local_root)
    session = boto_session(settings)
    return S3ObjectStore(
        settings.data_bucket,
        cast(Any, session).client("s3", region_name=settings.aws_region),
    )


class RawStore:
    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def land(
        self,
        key: SeasonKey,
        name: str,
        body: bytes,
        provenance: Provenance,
        suffix: str = "json",
    ) -> str:
        target = raw_key(provenance.source, key, raw_filename(name, provenance.fetched_at, suffix))
        if self.store.exists(target):
            # a replayed step function retry lands the same bytes again, that is not a conflict
            if self.store.get(target) == body:
                return target
            raise RawOverwriteError(target)
        uri = self.store.put(target, body)
        self.store.put(
            target + META_SUFFIX,
            json.dumps(provenance.model_dump(mode="json"), indent=2).encode(),
        )
        return uri

    def versions(self, source: Source, key: SeasonKey, name: str) -> list[str]:
        prefix = raw_key(source, key, name)
        return sorted(
            item.key
            for item in self.store.list(prefix)
            if not item.key.endswith(META_SUFFIX) and item.key.startswith(prefix)
        )

    def latest(self, source: Source, key: SeasonKey, name: str) -> tuple[bytes, Provenance] | None:
        found = self.versions(source, key, name)
        if not found:
            return None
        target = found[-1]
        meta = Provenance.model_validate_json(self.store.get(target + META_SUFFIX))
        return self.store.get(target), meta


def write_bronze(
    store: ObjectStore, table: str, key: SessionKey | EventKey, rows: list[Any]
) -> str:
    """Writes one partition. Callers with rows from several events group them first."""
    frame = pl.DataFrame([row.model_dump() for row in rows], infer_schema_length=None)
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    return store.put(bronze_key(table, key), buffer.getvalue())


def write_quarantine(
    store: ObjectStore, table: str, key: SessionKey | EventKey, run_id: str, rows: list[Any]
) -> str | None:
    if not rows:
        return None
    body = "\n".join(json.dumps(row.model_dump(mode="json")) for row in rows).encode()
    return store.put(quarantine_key(table, key, run_id), body)


def write_bronze_by_event(store: ObjectStore, table: str, rows: list[Any]) -> list[str]:
    grouped: dict[tuple[int, int], list[Any]] = {}
    for row in rows:
        grouped.setdefault((row.season, row.round), []).append(row)
    return [
        write_bronze(store, table, EventKey(season=season, round=round_), group)
        for (season, round_), group in sorted(grouped.items())
    ]
