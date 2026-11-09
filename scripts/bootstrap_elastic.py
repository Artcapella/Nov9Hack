"""Create the NETwork Elastic indices and load the clearly-labeled demo cohort."""
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.store import NetworkStore


async def main() -> None:
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Set ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY in .env first.")
    store = NetworkStore(settings)
    await store.start()
    try:
        await store.ensure_indices()
        records = json.loads(Path("data/seed_profiles.json").read_text(encoding="utf-8"))
        synthetic = Path("data/synthetic_profiles.json")
        if synthetic.exists():
            records += json.loads(synthetic.read_text(encoding="utf-8"))
        print(f"Seeded {await store.seed(records)} SynthDemo profiles into {settings.elasticsearch_index_prefix}_* indices.")
        print(f"Pinned inference endpoint inspected: {settings.elastic_inference_id}")
        print("Next: start API and worker, scan the event QR, and collect explicit attendee consent.")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
