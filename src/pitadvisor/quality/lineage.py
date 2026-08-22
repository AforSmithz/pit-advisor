import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pitadvisor.ingest.raw_store import META_SUFFIX, ObjectStore
from pitadvisor.types import Layer, Source

MANIFEST = Path("transform/target/manifest.json")

# every bronze table, the upstream it came from and the raw filename it lands under
RAW_ORIGIN: dict[str, tuple[Source, str]] = {
    "races": (Source.JOLPICA, "races"),
    "results": (Source.JOLPICA, "results"),
    "qualifying": (Source.JOLPICA, "qualifying"),
    "laps": (Source.JOLPICA, "laps"),
    "pitstops": (Source.JOLPICA, "pitstops"),
    "weather": (Source.OPEN_METEO, "weather"),
    "session_laps": (Source.FASTF1, "session_laps"),
}


class MissingManifestError(FileNotFoundError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} not found, run dbt build or dbt compile first")


class Trace(BaseModel, frozen=True):
    model: str
    sources: list[str]
    raw_objects: dict[str, int]

    @property
    def ok(self) -> bool:
        return bool(self.sources) and all(count > 0 for count in self.raw_objects.values())

    @property
    def detail(self) -> str:
        if not self.sources:
            return "reaches no bronze source"
        empty = [table for table, count in self.raw_objects.items() if count == 0]
        if empty:
            return f"{', '.join(empty)} has nothing in raw/"
        counted = ", ".join(
            f"{table} ({count})" for table, count in sorted(self.raw_objects.items())
        )
        return f"traced to {counted}"


def load(path: Path = MANIFEST) -> dict[str, Any]:
    if not path.is_file():
        raise MissingManifestError(path)
    return json.loads(path.read_text())


def _walk(manifest: dict[str, Any], node_id: str, seen: set[str]) -> set[str]:
    if node_id in seen:
        return set()
    seen.add(node_id)
    if node_id.startswith("source."):
        source: dict[str, Any] = manifest["sources"].get(node_id, {})
        return {str(source.get("name", node_id.rsplit(".", 1)[-1]))}
    node: dict[str, Any] = manifest["nodes"].get(node_id, {})
    parents: list[str] = node.get("depends_on", {}).get("nodes", [])
    found: set[str] = set()
    for parent in parents:
        found |= _walk(manifest, parent, seen)
    return found


def sources_of(manifest: dict[str, Any], node_id: str) -> set[str]:
    return _walk(manifest, node_id, set())


def gold_models(manifest: dict[str, Any]) -> list[str]:
    return sorted(
        node_id
        for node_id, node in manifest["nodes"].items()
        if node.get("resource_type") == "model" and "gold" in node.get("tags", [])
    )


def raw_count(store: ObjectStore, table: str) -> int:
    origin = RAW_ORIGIN.get(table)
    if origin is None:
        return 0
    source, name = origin
    return sum(
        1
        for item in store.list(f"{Layer.RAW}/source={source}/")
        if not item.key.endswith(META_SUFFIX) and item.key.rsplit("/", 1)[-1].startswith(f"{name}-")
    )


def trace(store: ObjectStore, manifest: dict[str, Any] | None = None) -> list[Trace]:
    loaded = manifest if manifest is not None else load()
    counted: dict[str, int] = {}
    traces: list[Trace] = []
    for node_id in gold_models(loaded):
        tables = sorted(sources_of(loaded, node_id))
        for table in tables:
            if table not in counted:
                counted[table] = raw_count(store, table)
        traces.append(
            Trace(
                model=loaded["nodes"][node_id]["name"],
                sources=tables,
                raw_objects={table: counted[table] for table in tables},
            )
        )
    return traces
