"""Read-only encrypted export of every NETwork index, including legacy data.

Decrypt with the existing CONTACT_ENCRYPTION_KEY. Keep that key separately.
This is a logical export; semantic fields can require re-inference on restore.
"""
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_scan

from app.config import get_settings


async def main():
    settings = get_settings()
    if not settings.elastic_enabled or not settings.contact_encryption_key:
        raise SystemExit("Elasticsearch and CONTACT_ENCRYPTION_KEY are required")
    logging.getLogger("elastic_transport").setLevel(logging.CRITICAL)
    cipher = Fernet(settings.contact_encryption_key.encode())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path("backups") / stamp
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": stamp, "kind": "encrypted logical export", "complete": False, "indices": {}}
    async with AsyncElasticsearch(settings.elasticsearch_url, api_key=settings.elasticsearch_api_key,
                                  verify_certs=settings.elasticsearch_verify_certs, request_timeout=60) as client:
        definitions = await client.indices.get(index=f"{settings.elasticsearch_index_prefix}_*")
        for name, definition in definitions.items():
            rows = [hit async for hit in async_scan(client, index=name, query={"query": {"match_all": {}}},
                                                    size=250, scroll="5m")]
            payload = json.dumps({"index": name, "definition": definition, "documents": rows}).encode()
            encrypted = cipher.encrypt(payload)
            filename = name + ".json.fernet"
            (destination / filename).write_bytes(encrypted)
            # Verify the bytes on disk, not just the in-memory buffer.
            restored = json.loads(cipher.decrypt((destination / filename).read_bytes()))
            assert len(restored["documents"]) == len(rows)
            manifest["indices"][name] = {"documents": len(rows), "file": filename,
                                         "sha256": hashlib.sha256(encrypted).hexdigest()}
    manifest["complete"] = True
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"backup": str(destination), "verified": True, "indices": len(manifest["indices"]),
                      "documents": sum(item["documents"] for item in manifest["indices"].values())}))


if __name__ == "__main__":
    asyncio.run(main())
