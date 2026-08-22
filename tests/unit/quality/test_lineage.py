import json

import pytest

from pitadvisor.quality import lineage


def manifest(gold_sources=("results", "races"), extra_model=True):
    nodes = {
        "model.pitadvisor.silver_results": {
            "resource_type": "model",
            "name": "silver_results",
            "tags": ["silver"],
            "depends_on": {"nodes": [f"source.pitadvisor.bronze.{name}" for name in gold_sources]},
        },
        "model.pitadvisor.gold_race_results": {
            "resource_type": "model",
            "name": "gold_race_results",
            "tags": ["gold"],
            "depends_on": {
                "nodes": ["model.pitadvisor.silver_results"] if extra_model else [],
            },
        },
    }
    sources = {
        f"source.pitadvisor.bronze.{name}": {"name": name, "resource_type": "source"}
        for name in gold_sources
    }
    return {"nodes": nodes, "sources": sources}


def land(store, source, name):
    store.put(f"raw/source={source}/season=2024/round=05/{name}-20240505T000000000Z.json", b"{}")
    store.put(
        f"raw/source={source}/season=2024/round=05/{name}-20240505T000000000Z.json.meta.json", b"{}"
    )


def test_a_gold_model_reaches_the_sources_under_its_silver():
    found = lineage.sources_of(manifest(), "model.pitadvisor.gold_race_results")
    assert found == {"results", "races"}


def test_only_gold_models_are_traced():
    assert lineage.gold_models(manifest()) == ["model.pitadvisor.gold_race_results"]


def test_the_meta_files_are_not_counted_as_raw(store):
    land(store, "jolpica", "results")
    assert lineage.raw_count(store, "results") == 1


def test_a_traced_model_needs_raw_under_every_source(store):
    land(store, "jolpica", "results")
    land(store, "jolpica", "races")
    traces = lineage.trace(store, manifest())
    assert traces[0].ok
    assert "results (1)" in traces[0].detail


def test_a_source_with_no_raw_fails_the_trace(store):
    land(store, "jolpica", "results")
    trace = lineage.trace(store, manifest())[0]
    assert not trace.ok
    assert "races has nothing in raw/" in trace.detail


def test_a_gold_model_built_on_nothing_fails(store):
    trace = lineage.trace(store, manifest(extra_model=False))[0]
    assert not trace.ok
    assert trace.detail == "reaches no bronze source"


def test_a_missing_manifest_says_to_run_dbt(tmp_path):
    with pytest.raises(lineage.MissingManifestError):
        lineage.load(tmp_path / "manifest.json")


def test_the_real_manifest_shape_parses(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()))
    assert lineage.gold_models(lineage.load(path))
