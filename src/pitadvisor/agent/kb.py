import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, cast

from pydantic import BaseModel

from pitadvisor.ingest.docs import METADATA_SUFFIX
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.types import Layer

CHUNK_CHARACTERS: Final = 1200
STOP_WORDS: Final = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "with",
    )
)


@dataclass(frozen=True)
class Passage:
    text: str
    uri: str
    score: float
    metadata: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "uri": self.uri,
            "score": round(self.score, 4),
            "title": self.metadata.get("title", ""),
            "source": self.metadata.get("source", ""),
        }


class KnowledgeBase:
    """Bedrock Knowledge Base over the S3 Vectors index built from docs/."""

    def __init__(self, client: Any, knowledge_base_id: str) -> None:
        self.client = client
        self.knowledge_base_id = knowledge_base_id

    def retrieve(
        self, query: str, top_k: int = 5, source: str | None = None
    ) -> list[dict[str, Any]]:
        search: dict[str, Any] = {"numberOfResults": top_k}
        if source is not None:
            search["filter"] = {"equals": {"key": "source", "value": source}}
        response: dict[str, Any] = self.client.retrieve(
            knowledgeBaseId=self.knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": search},
        )
        return [_passage(item).as_dict() for item in response.get("retrievalResults", [])]


def _passage(item: dict[str, Any]) -> Passage:
    location = item.get("location", {}).get("s3Location", {})
    return Passage(
        text=str(item.get("content", {}).get("text", "")),
        uri=str(location.get("uri", "")),
        score=float(item.get("score", 0.0)),
        metadata={str(key): value for key, value in item.get("metadata", {}).items()},
    )


class IngestionError(RuntimeError):
    pass


class Ingestion(BaseModel, frozen=True):
    job_id: str
    status: str
    scanned: int = 0
    indexed: int = 0
    failed: int = 0


def start_ingestion(
    client: Any,
    knowledge_base_id: str,
    data_source_id: str,
    wait: bool = True,
    timeout_seconds: float = 900.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Ingestion:
    """Tell bedrock to reindex docs/. Nothing else does: writing a document to the lake does
    not put it in the vector index."""
    started: dict[str, Any] = client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id
    )
    job = started["ingestionJob"]
    if not wait:
        return _ingestion(job)
    deadline = time.monotonic() + timeout_seconds
    while str(job["status"]) in {"STARTING", "IN_PROGRESS"}:
        if time.monotonic() > deadline:
            raise IngestionError(f"ingestion job {job['ingestionJobId']} has not finished")
        sleep(5.0)
        job = client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job["ingestionJobId"],
        )["ingestionJob"]
    result = _ingestion(job)
    if result.status != "COMPLETE":
        raise IngestionError(f"ingestion finished as {result.status}")
    return result


def _ingestion(job: dict[str, Any]) -> Ingestion:
    statistics = job.get("statistics", {})
    return Ingestion(
        job_id=str(job["ingestionJobId"]),
        status=str(job["status"]),
        scanned=int(statistics.get("numberOfDocumentsScanned", 0)),
        indexed=int(statistics.get("numberOfNewDocumentsIndexed", 0)),
        failed=int(statistics.get("numberOfDocumentsFailed", 0)),
    )


def _terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOP_WORDS]


def chunks(text: str, size: int = CHUNK_CHARACTERS) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in text.split("\n"):
        if length + len(paragraph) > size and current:
            out.append("\n".join(current).strip())
            current, length = [], 0
        current.append(paragraph)
        length += len(paragraph) + 1
    if current:
        out.append("\n".join(current).strip())
    return [chunk for chunk in out if chunk]


class LocalCorpus:
    """The same corpus, scored locally. Not the retriever the eval gate measures, but a real
    one: it reads the documents the knowledge base is built from, not a canned answer."""

    def __init__(self, store: ObjectStore, prefix: str = f"{Layer.DOCS}/") -> None:
        self.store = store
        self.prefix = prefix
        self._loaded: list[tuple[str, str, dict[str, Any]]] = []
        self._frequencies: Counter[str] = Counter()

    def _load(self) -> list[tuple[str, str, dict[str, Any]]]:
        if self._loaded:
            return self._loaded
        for item in sorted(self.store.list(self.prefix), key=lambda o: o.key):
            if item.key.endswith(METADATA_SUFFIX):
                continue
            try:
                text = self.store.get(item.key).decode()
            except UnicodeDecodeError:
                continue
            metadata = self._metadata(item.key)
            for chunk in chunks(text):
                self._loaded.append((chunk, item.key, metadata))
                self._frequencies.update(set(_terms(chunk)))
        return self._loaded

    def _metadata(self, key: str) -> dict[str, Any]:
        try:
            payload = json.loads(self.store.get(key + METADATA_SUFFIX))
        except Exception:
            return {}
        return {str(name): value for name, value in payload.get("metadataAttributes", {}).items()}

    def retrieve(
        self, query: str, top_k: int = 5, source: str | None = None
    ) -> list[dict[str, Any]]:
        documents = self._load()
        total = len(documents) or 1
        wanted = _terms(query)
        scored: list[Passage] = []
        for text, key, metadata in documents:
            if source is not None and metadata.get("source") != source:
                continue
            counts = Counter(_terms(text))
            # idf-weighted overlap, smoothed so a match always scores above zero. a raw idf
            # goes to zero on a two-document corpus and hides every hit
            score = sum(
                counts[term] * math.log(1 + total / (1 + self._frequencies[term]))
                for term in wanted
                if counts[term]
            )
            if score > 0:
                scored.append(Passage(text=text, uri=key, score=score, metadata=metadata))
        scored.sort(key=lambda passage: passage.score, reverse=True)
        return [passage.as_dict() for passage in scored[:top_k]]
