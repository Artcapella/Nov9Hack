"""python -m scripts.import_resumes --manifest /private/path/sources.json"""
import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.ingestion import import_source
from app.store import NetworkStore
from app.worker import process_pending


async def run(path: str):
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Corpus imports require Elasticsearch")
    store = NetworkStore(settings)
    await store.start()
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
        results = []
        for entry in entries:
            try:
                result = await import_source(store, entry)
                results.append(result)
            except Exception:
                raise SystemExit("Import failed; verify the manifest, allowlist, permissions and source availability") from None
        await process_pending(store)
        from datetime import UTC, datetime
        verified = []
        for result in results:
            job = await store.get("ingestion_jobs", result["job_id"])
            vector = await store.get("resume_vectors", result["source_id"])
            if not job or job["state"] != "indexed" or not vector:
                raise SystemExit("Corpus publication is not complete; inspect the worker job status")
            verified.append({**result, "state": job["state"], "model_id": vector["model_id"]})
        evidence = {"checked_at": datetime.now(UTC).isoformat(), "schema": settings.elastic_schema_version,
                    "records": verified, "attendee_discovery_created": False}
        output = Path("docs/evidence/corpus-import.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence))
    finally:
        await store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.manifest))
