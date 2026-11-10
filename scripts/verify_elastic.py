"""Read-only connectivity and inference inspection; prints no URLs or keys."""
import asyncio
import json
import logging

from elasticsearch import AsyncElasticsearch

from app.config import get_settings


async def main():
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Elasticsearch is not configured")
    logging.getLogger("elastic_transport").setLevel(logging.CRITICAL)
    async with AsyncElasticsearch(settings.elasticsearch_url, api_key=settings.elasticsearch_api_key,
                                   verify_certs=settings.elasticsearch_verify_certs, request_timeout=15, max_retries=0) as client:
        try:
            reachable = await client.ping()
            if not reachable:
                raise SystemExit("Elasticsearch connection/authentication could not be verified")
            endpoints = await client.inference.get()
            inspected = [{key: endpoint.get(key) for key in ("inference_id", "task_type", "service")}
                         for endpoint in endpoints.get("endpoints", [])]
            print(json.dumps({"connected": True, "inference_endpoints": inspected,
                              "contact_encryption_configured": bool(settings.contact_encryption_key)}))
            if await client.indices.exists(index=settings.index("user_vectors")):
                mapping = await client.indices.get_mapping(index=settings.index("user_vectors"))
                print(json.dumps({"profile_indices": {index: list(value["mappings"].get("properties", {})) for index, value in mapping.items()}}))
        except SystemExit:
            raise
        except Exception:
            raise SystemExit("Elasticsearch inference inspection unavailable; check connectivity, credentials and privileges") from None


if __name__ == "__main__":
    asyncio.run(main())
