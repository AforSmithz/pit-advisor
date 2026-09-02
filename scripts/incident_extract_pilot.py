"""Runs the model over the stewards' documents that have no field block, and reports how many
of the values it returns are actually in the source. Nothing here decides anything: the point
is the verification rate, so the cost of extracting the other two hundred is known before it is
spent. Writes one json line per document and skips documents already recorded, so a stopped run
resumes."""

import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from botocore.config import Config

from pitadvisor.config import Settings, boto_session
from pitadvisor.incidents.extract import extract
from pitadvisor.incidents.parse import parse
from pitadvisor.ingest.docs import pdf_text

INCIDENT_KINDS = ("decision", "offence", "infringement")
SEED = 20260902


def prose(cache: Path, client: Any, bucket: str) -> list[str]:
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix="raw/source=fia_docs/"
    ):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.endswith(".pdf") and "/round=" in key:
                keys.append(key)
    found: list[str] = []
    for key in keys:
        name = key.rsplit("/", 1)[1]
        if not any(f"-{kind}-" in name for kind in INCIDENT_KINDS):
            continue
        text = text_of(cache, client, bucket, key)
        if text and not parse(text).structured:
            found.append(key)
    return found


def text_of(cache: Path, client: Any, bucket: str, key: str) -> str:
    landed = cache / (key.replace("/", "__") + ".txt")
    if landed.exists():
        return landed.read_text()
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    text = pdf_text(body)
    landed.write_text(text)
    return text


def main() -> None:
    cache, out = Path(sys.argv[1]), Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    cache.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    session = cast(Any, boto_session(settings))
    s3 = session.client("s3", region_name=settings.aws_region)
    bedrock = session.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 8, "mode": "adaptive"}),
    )

    keys = prose(cache, s3, settings.data_bucket)
    print(f"{len(keys)} documents with no field block", flush=True)
    random.Random(SEED).shuffle(keys)

    done: set[str] = set()
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines()}
    todo = [key for key in keys if key not in done][:limit]
    print(f"{len(done)} already recorded, running {len(todo)}", flush=True)

    def run(key: str) -> dict[str, Any]:
        text = text_of(cache, s3, settings.data_bucket, key)
        found = extract(bedrock, settings.bedrock_model, text)
        return {
            "key": key,
            "entries": len(found.decisions),
            "unverified": found.unverified,
            "input_tokens": found.input_tokens,
            "output_tokens": found.output_tokens,
            "error": found.error,
            "decisions": [d.model_dump(mode="json") for d in found.decisions],
        }

    with out.open("a") as sink, ThreadPoolExecutor(max_workers=4) as pool:
        for record in pool.map(run, todo):
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            mark = "ok   " if not record["unverified"] and not record["error"] else "CHECK"
            print(
                f"{mark} {record['entries']:2d} entries  {record['key'].rsplit('/', 1)[1][:58]}"
                f"  {record['unverified'] or ''}",
                flush=True,
            )

    records = [json.loads(line) for line in out.read_text().splitlines()]
    spans = sum(len(d["spans"]) for r in records for d in r["decisions"])
    bad = sum(len(r["unverified"]) for r in records)
    clean = sum(1 for r in records if not r["unverified"] and not r["error"])
    tokens_in = sum(r["input_tokens"] for r in records)
    tokens_out = sum(r["output_tokens"] for r in records)
    print(f"\n{len(records)} documents, {clean} with every value verified")
    print(f"{spans} values, {bad} not found in the source")
    print(f"{tokens_in} input tokens, {tokens_out} output tokens")
    # haiku 4.5 list price, us-east-1
    print(f"about ${tokens_in / 1e6 * 1.0 + tokens_out / 1e6 * 5.0:.3f} so far")


if __name__ == "__main__":
    main()
