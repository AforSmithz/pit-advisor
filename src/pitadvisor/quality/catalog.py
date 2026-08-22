from datetime import date, datetime
from enum import StrEnum
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from pitadvisor.quality.contracts import TABLES, BronzeRow
from pitadvisor.types import Layer, SessionKind

# projection needs a finite range and every season we could ever hold fits in this one
SEASON_RANGE = (2018, 2035)
ROUND_RANGE = (1, 30)

PARTITIONED_BY_SESSION = ("session_laps",)

HIVE_TYPES: dict[type, str] = {
    bool: "boolean",
    int: "bigint",
    float: "double",
    str: "string",
    datetime: "timestamp",
    date: "date",
}

SERDE = "org.apache.hadoop.hive.serde2.parquet.MapredParquetSerDe"
INPUT_FORMAT = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
OUTPUT_FORMAT = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"


class UnmappedTypeError(TypeError):
    def __init__(self, table: str, column: str, annotation: Any) -> None:
        super().__init__(f"{table}.{column} has no hive type for {annotation!r}")


def hive_type(annotation: Any) -> str | None:
    if get_origin(annotation) in (Union, UnionType):
        options = [arg for arg in get_args(annotation) if arg is not type(None)]
        return hive_type(options[0]) if len(options) == 1 else None
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return "string"
    if isinstance(annotation, type):
        return HIVE_TYPES.get(annotation)
    return None


def partition_keys(table: str) -> tuple[str, ...]:
    return (
        ("season", "round", "session") if table in PARTITIONED_BY_SESSION else ("season", "round")
    )


def columns(table: str, model: type[BronzeRow]) -> list[dict[str, str]]:
    keys = partition_keys(table)
    listed: list[dict[str, str]] = []
    fields: dict[str, FieldInfo] = model.model_fields
    for name, field in fields.items():
        if name in keys:
            continue
        mapped = hive_type(field.annotation)
        if mapped is None:
            raise UnmappedTypeError(table, name, field.annotation)
        listed.append({"Name": name, "Type": mapped})
    return listed


def projection(table: str, location: str) -> dict[str, str]:
    template = f"{location}season=${{season}}/round=${{round}}/"
    properties = {
        "projection.enabled": "true",
        "projection.season.type": "integer",
        "projection.season.range": f"{SEASON_RANGE[0]},{SEASON_RANGE[1]}",
        "projection.round.type": "integer",
        "projection.round.range": f"{ROUND_RANGE[0]},{ROUND_RANGE[1]}",
        # the writer zero-pads the round, the projected column stays an integer
        "projection.round.digits": "2",
    }
    if "session" in partition_keys(table):
        properties["projection.session.type"] = "enum"
        properties["projection.session.values"] = ",".join(kind.value for kind in SessionKind)
        template += "session=${session}/"
    properties["storage.location.template"] = template
    return properties


def table_input(table: str, model: type[BronzeRow], bucket: str) -> dict[str, Any]:
    location = f"s3://{bucket}/{Layer.BRONZE}/table={table}/"
    keys = partition_keys(table)
    return {
        "Name": table,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "EXTERNAL": "TRUE",
            "classification": "parquet",
            "pitadvisor.layer": Layer.BRONZE.value,
            **projection(table, location),
        },
        # season and round live in the object key and in the file, glue may only see them once
        "PartitionKeys": [
            {"Name": name, "Type": "string" if name == "session" else "bigint"} for name in keys
        ],
        "StorageDescriptor": {
            "Columns": columns(table, model),
            "Location": location,
            "InputFormat": INPUT_FORMAT,
            "OutputFormat": OUTPUT_FORMAT,
            "SerdeInfo": {"SerializationLibrary": SERDE},
            "Compressed": True,
        },
    }


def definitions(bucket: str) -> dict[str, dict[str, Any]]:
    return {table: table_input(table, model, bucket) for table, model in TABLES.items()}


class CatalogAction(BaseModel, frozen=True):
    table: str
    action: str
    detail: str = ""


def _existing(glue: Any, database: str, table: str) -> dict[str, Any] | None:
    try:
        return dict(glue.get_table(DatabaseName=database, Name=table)["Table"])
    except Exception:
        return None


def _drifted(current: dict[str, Any], wanted: dict[str, Any]) -> str:
    listed: list[str] = []
    columns_now = current.get("StorageDescriptor", {}).get("Columns", [])
    if columns_now != wanted["StorageDescriptor"]["Columns"]:
        listed.append("columns")
    if current.get("PartitionKeys", []) != wanted["PartitionKeys"]:
        listed.append("partition keys")
    if (
        current.get("StorageDescriptor", {}).get("Location")
        != wanted["StorageDescriptor"]["Location"]
    ):
        listed.append("location")
    parameters = current.get("Parameters", {})
    if any(parameters.get(key) != value for key, value in wanted["Parameters"].items()):
        listed.append("projection")
    return ", ".join(listed)


def sync(glue: Any, database: str, bucket: str, apply: bool = True) -> list[CatalogAction]:
    actions: list[CatalogAction] = []
    for table, wanted in definitions(bucket).items():
        current = _existing(glue, database, table)
        if current is None:
            if apply:
                glue.create_table(DatabaseName=database, TableInput=wanted)
            actions.append(CatalogAction(table=table, action="create"))
            continue
        drift = _drifted(current, wanted)
        if not drift:
            actions.append(CatalogAction(table=table, action="unchanged"))
            continue
        if apply:
            glue.update_table(DatabaseName=database, TableInput=wanted)
        actions.append(CatalogAction(table=table, action="update", detail=drift))
    return actions
