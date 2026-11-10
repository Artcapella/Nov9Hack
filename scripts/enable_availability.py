"""Apply only the additive availability date mapping; never seed or rewrite cards."""
import asyncio

from app.config import get_settings
from app.store import NetworkStore


async def main():
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Elasticsearch configuration is required")
    store = NetworkStore(settings)
    await store.start()
    try:
        await store.client.indices.put_mapping(index=settings.index("user_vectors"),
                                              properties={"available_until": {"type": "date"}})
        print("Availability mapping applied; existing attendee data preserved.")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
