import json

from pitadvisor.agent.kb import KnowledgeBase, LocalCorpus, chunks

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
