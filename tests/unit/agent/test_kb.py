import json

import pytest

from pitadvisor.agent.kb import (
    IngestionError,
    KnowledgeBase,
    LocalCorpus,
    chunks,
    start_ingestion,
)

SPORTING = (
    "Parc ferme conditions begin when the car leaves the pit lane for qualifying. "
    "Changing a front wing angle is permitted; anything else needs approval."
)
PREVIEW = "Monza rewards low drag. Teams bring a dedicated rear wing for this race alone."


def land(store, key, text, attributes):
    store.put(key, text.encode())
    store.put(
        key + ".metadata.json",
        json.dumps({"metadataAttributes": attributes}).encode(),
    )


def corpus(store):
    land(
        store,
        "docs/source=fia_docs/kind=regulation/sporting.txt",
        SPORTING,
        {"source": "fia_docs", "title": "Sporting Regulations"},
    )
    land(
        store,
        "docs/source=wikipedia/kind=race/monza.txt",
        PREVIEW,
        {"source": "wikipedia", "title": "Italian Grand Prix"},
    )
    return LocalCorpus(store)


def test_the_passage_that_answers_the_question_ranks_first(store):
    found = corpus(store).retrieve("parc ferme", top_k=2)
    assert "Parc ferme" in found[0]["text"]
    assert found[0]["title"] == "Sporting Regulations"


def test_a_query_that_matches_nothing_returns_nothing(store):
    assert corpus(store).retrieve("hydrogen powertrain") == []


def test_the_source_filter_keeps_the_other_corpus_out(store):
    found = corpus(store).retrieve("wing", source="wikipedia")
    assert [item["source"] for item in found] == ["wikipedia"]


def test_every_passage_carries_the_key_it_came_from(store):
    assert corpus(store).retrieve("parc ferme")[0]["uri"].startswith("docs/source=fia_docs/")


def test_metadata_sidecars_are_not_retrievable_documents(store):
    found = corpus(store).retrieve("sporting regulations parc ferme", top_k=10)
    assert all(not item["uri"].endswith(".metadata.json") for item in found)


def test_long_text_is_split_into_passages():
    parts = chunks("\n".join(f"paragraph {n} " + "x" * 200 for n in range(20)), size=500)
    assert len(parts) > 1
    assert all(len(part) < 900 for part in parts)


class FakeRetrieve:
    def __init__(self):
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "retrievalResults": [
                {
                    "content": {"text": "Parc ferme opens after qualifying."},
                    "location": {"s3Location": {"uri": "s3://lake/docs/sporting.txt"}},
                    "score": 0.61,
                    "metadata": {"title": "Sporting Regulations", "source": "fia_docs"},
                }
            ]
        }


def test_the_knowledge_base_maps_a_retrieval_into_passages():
    found = KnowledgeBase(FakeRetrieve(), "kb-1").retrieve("parc ferme")
    assert found == [
        {
            "text": "Parc ferme opens after qualifying.",
            "uri": "s3://lake/docs/sporting.txt",
            "score": 0.61,
            "title": "Sporting Regulations",
            "source": "fia_docs",
        }
    ]


def test_the_knowledge_base_passes_the_source_filter_through():
    client = FakeRetrieve()
    KnowledgeBase(client, "kb-1").retrieve("wing", top_k=3, source="wikipedia")
    search = client.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert search["numberOfResults"] == 3
    assert search["filter"] == {"equals": {"key": "source", "value": "wikipedia"}}


def test_without_a_filter_none_is_sent():
    client = FakeRetrieve()
    KnowledgeBase(client, "kb-1").retrieve("wing")
    assert "filter" not in client.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]


class FakeIngest:
    def __init__(self, statuses=("STARTING", "COMPLETE")):
        self.statuses = list(statuses)
        self.calls = []

    def _job(self):
        return {
            "ingestionJob": {
                "ingestionJobId": "job-1",
                "status": self.statuses.pop(0),
                "statistics": {
                    "numberOfDocumentsScanned": 12,
                    "numberOfNewDocumentsIndexed": 12,
                    "numberOfDocumentsFailed": 0,
                },
            }
        }

    def start_ingestion_job(self, **kwargs):
        self.calls.append(("start", kwargs))
        return self._job()

    def get_ingestion_job(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self._job()


def test_an_ingestion_run_waits_for_the_job_and_reports_what_it_indexed():
    job = start_ingestion(FakeIngest(), "kb-1", "ds-1", sleep=lambda _: None)
    assert job.status == "COMPLETE"
    assert job.indexed == 12


def test_the_ingestion_names_both_ids():
    client = FakeIngest()
    start_ingestion(client, "kb-1", "ds-1", sleep=lambda _: None)
    assert client.calls[0][1] == {"knowledgeBaseId": "kb-1", "dataSourceId": "ds-1"}


def test_a_failed_ingestion_is_an_error_not_a_quiet_return():
    with pytest.raises(IngestionError):
        start_ingestion(FakeIngest(("STARTING", "FAILED")), "kb-1", "ds-1", sleep=lambda _: None)


def test_a_job_that_never_finishes_times_out():
    with pytest.raises(IngestionError):
        start_ingestion(
            FakeIngest(("IN_PROGRESS",) * 40),
            "kb-1",
            "ds-1",
            timeout_seconds=0.0,
            sleep=lambda _: None,
        )


def test_the_caller_can_start_a_job_without_waiting():
    job = start_ingestion(FakeIngest(("STARTING",)), "kb-1", "ds-1", wait=False)
    assert job.status == "STARTING"
