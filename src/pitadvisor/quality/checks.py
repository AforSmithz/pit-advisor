import io
import json
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
import polars as pl
from pydantic import BaseModel

from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.quality.contracts import TABLES, Reason
from pitadvisor.types import Layer

MAX_AGE = timedelta(days=7)

NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "races": ("season", "round"),
    "results": ("season", "round", "driver_id"),
    "qualifying": ("season", "round", "driver_id"),
    "laps": ("season", "round", "driver_id", "lap"),
    "pitstops": ("season", "round", "driver_id", "stop"),
    "weather": ("season", "round", "circuit_id", "observed_at", "is_forecast"),
    "session_laps": ("season", "round", "session", "driver_code", "lap"),
}

# columns that are nullable in the contract but should be nearly always present in practice
NULL_WATCH: dict[str, tuple[str, ...]] = {
    "pitstops": ("duration_millis",),
    "results": ("position",),
    "session_laps": ("lap_time_millis",),
}
NULL_WARN_RATE = 0.05

# every table on the left must have its drivers accounted for in the race result. qualifying is
# scoped to the season because a driver can qualify and then not start, which is not a defect
REFERENCES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("laps", "results", ("season", "round")),
    ("pitstops", "results", ("season", "round")),
    ("qualifying", "results", ("season",)),
)


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class Outcome(BaseModel, frozen=True):
    check: str
    table: str
    status: Status
    detail: str


class Diagnostic(BaseModel, frozen=True):
    name: str
    table: str
    value: int
    detail: str


class QuarantineCount(BaseModel, frozen=True):
    table: str
    reason: str
    rows: int
    explained: bool


class QualityReport(BaseModel, frozen=True):
    layer: Layer
    generated_at: datetime
    outcomes: list[Outcome]
    quarantine: list[QuarantineCount]
    diagnostics: list[Diagnostic] = []

    @property
    def ok(self) -> bool:
        failed = any(outcome.status is Status.FAIL for outcome in self.outcomes)
        return not failed and not any(item.explained is False for item in self.quarantine)


def read_table(store: ObjectStore, layer: Layer, table: str) -> pl.DataFrame | None:
    frames: list[pl.DataFrame] = []
    for item in store.list(f"{layer}/table={table}/"):
        if item.key.endswith(".parquet"):
            frames.append(pl.read_parquet(io.BytesIO(store.get(item.key))))
    if not frames:
        return None
    return pl.concat(frames, how="diagonal_relaxed")


@contextmanager
def staged(
    store: ObjectStore, layer: Layer
) -> Generator[tuple[duckdb.DuckDBPyConnection, set[str]]]:
    """Copies a layer's parquet out of the store so duckdb can read it as files."""
    connection = duckdb.connect()
    with TemporaryDirectory(prefix="pitadv-") as scratch:
        root = Path(scratch)
        present: set[str] = set()
        for table in TABLES:
            files: list[str] = []
            for index, item in enumerate(store.list(f"{layer}/table={table}/")):
                if not item.key.endswith(".parquet"):
                    continue
                path = root / f"{table}-{index}.parquet"
                path.write_bytes(store.get(item.key))
                files.append(str(path))
            if not files:
                continue
            listed = ", ".join(f"'{name}'" for name in files)
            connection.execute(
                f"create view {table} as select * from read_parquet([{listed}], union_by_name=true)"
            )
            present.add(table)
        try:
            yield connection, present
        finally:
            connection.close()


def columns_of(connection: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    described = connection.execute(f"select * from {table} limit 0").description or []
    return {str(column[0]) for column in described}


def quarantine_counts(store: ObjectStore) -> list[QuarantineCount]:
    known = {reason.value for reason in Reason}
    tally: Counter[tuple[str, str]] = Counter()
    for item in store.list(f"{Layer.QUARANTINE}/"):
        if not item.key.endswith(".jsonl"):
            continue
        for line in store.get(item.key).decode().splitlines():
            if not line.strip():
                continue
            row: dict[str, Any] = json.loads(line)
            tally[(str(row.get("table")), str(row.get("reason")))] += 1
    return [
        QuarantineCount(table=table, reason=reason, rows=rows, explained=reason in known)
        for (table, reason), rows in sorted(tally.items())
    ]


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = connection.execute(sql).fetchone()
    return row[0] if row else None


def check_freshness(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    now: datetime,
    max_age: timedelta,
) -> Outcome:
    # epoch() keeps this in floats: handing duckdb timestamps back to python wants pytz
    latest: Any = _scalar(connection, f"select epoch(max(ingested_at)) from {table}")
    if latest is None:
        return Outcome(check="freshness", table=table, status=Status.FAIL, detail="no ingested_at")
    age = now - datetime.fromtimestamp(float(latest), UTC)
    hours = age.total_seconds() / 3600
    if age > max_age:
        return Outcome(
            check="freshness",
            table=table,
            status=Status.WARN,
            detail=f"{hours:.1f}h old, over the {max_age.days}d threshold",
        )
    return Outcome(check="freshness", table=table, status=Status.OK, detail=f"{hours:.1f}h old")


def check_row_count(connection: duckdb.DuckDBPyConnection, table: str) -> Outcome:
    rows = int(_scalar(connection, f"select count(*) from {table}") or 0)
    status = Status.OK if rows > 0 else Status.FAIL
    return Outcome(check="row_count", table=table, status=status, detail=f"{rows} rows")


def check_duplicates(
    connection: duckdb.DuckDBPyConnection, table: str, columns: set[str]
) -> Outcome:
    keys = [key for key in NATURAL_KEYS[table] if key in columns]
    joined = ", ".join(keys)
    duplicated = int(
        _scalar(
            connection,
            f"select count(*) - count(distinct ({joined})) from {table}",
        )
        or 0
    )
    if duplicated:
        return Outcome(
            check="duplicate_key",
            table=table,
            status=Status.FAIL,
            detail=f"{duplicated} rows share a key of {'+'.join(keys)}",
        )
    return Outcome(
        check="duplicate_key", table=table, status=Status.OK, detail=f"unique on {'+'.join(keys)}"
    )


def check_nulls(
    connection: duckdb.DuckDBPyConnection, table: str, columns: set[str]
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    rows = int(_scalar(connection, f"select count(*) from {table}") or 0)
    for column in NULL_WATCH.get(table, ()):
        if column not in columns or not rows:
            continue
        missing = int(
            _scalar(connection, f"select count(*) from {table} where {column} is null") or 0
        )
        rate = missing / rows
        outcomes.append(
            Outcome(
                check="null_rate",
                table=table,
                status=Status.WARN if rate > NULL_WARN_RATE else Status.OK,
                detail=f"{missing}/{rows} rows have no {column}",
            )
        )
    return outcomes


def check_references(connection: duckdb.DuckDBPyConnection, present: set[str]) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for child, parent, scope in REFERENCES:
        if child not in present or parent not in present:
            continue
        on = " and ".join(f"p.{column} = c.{column}" for column in (*scope, "driver_id"))
        missing = int(
            _scalar(
                connection,
                f"""
                select count(*) from {child} c
                where not exists (select 1 from {parent} p where {on})
                """,
            )
            or 0
        )
        where = " and ".join(scope)
        outcomes.append(
            Outcome(
                check="referential",
                table=child,
                status=Status.FAIL if missing else Status.OK,
                detail=(
                    f"{missing} rows reference a driver missing from {parent}"
                    if missing
                    else f"every driver resolves in {parent} within the same {where}"
                ),
            )
        )
    return outcomes


def count_did_not_start(
    connection: duckdb.DuckDBPyConnection, present: set[str]
) -> list[Diagnostic]:
    if "qualifying" not in present or "results" not in present:
        return []
    withdrawn = int(
        _scalar(
            connection,
            """
            select count(*) from qualifying q
            where not exists (
                select 1 from results r
                where r.season = q.season and r.round = q.round
                  and r.driver_id = q.driver_id
            )
            """,
        )
        or 0
    )
    return [
        Diagnostic(
            name="did_not_start",
            table="qualifying",
            value=withdrawn,
            detail=(
                f"{withdrawn} driver{'' if withdrawn == 1 else 's'} qualified "
                "without appearing in the race result"
            ),
        )
    ]


def report(
    store: ObjectStore,
    layer: Layer = Layer.BRONZE,
    now: datetime | None = None,
    max_age: timedelta = MAX_AGE,
) -> QualityReport:
    stamp = now or datetime.now(UTC)
    outcomes: list[Outcome] = []
    diagnostics: list[Diagnostic] = []
    with staged(store, layer) as (connection, present):
        for table in sorted(present):
            outcomes.append(check_row_count(connection, table))
            outcomes.append(check_freshness(connection, table, stamp, max_age))
            columns = columns_of(connection, table)
            outcomes.append(check_duplicates(connection, table, columns))
            outcomes.extend(check_nulls(connection, table, columns))
        outcomes.extend(check_references(connection, present))
        diagnostics = count_did_not_start(connection, present)
        if not present:
            outcomes.append(
                Outcome(
                    check="row_count", table="-", status=Status.FAIL, detail=f"{layer}/ is empty"
                )
            )
    return QualityReport(
        layer=layer,
        generated_at=stamp,
        outcomes=outcomes,
        quarantine=quarantine_counts(store),
        diagnostics=diagnostics,
    )
