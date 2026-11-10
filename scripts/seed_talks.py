"""Create the talks index if it is missing and load the fictional demo schedule. Touches no other index."""
import asyncio

from app.config import get_settings
from app.elastic_schema import mappings
from app.store import NetworkStore
from app.talks import documents


async def main():
    settings = get_settings()
    store = NetworkStore(settings)
    await store.start()
    try:
        alias = settings.index("talks")
        if not await store.client.indices.exists(index=alias):
            await store.client.indices.create(index=f"{alias}-000001", mappings=mappings(settings)["talks"],
                                              aliases={alias: {"is_write_index": True}})
        operations = []
        for doc in documents(settings.demo_event_id):
            operations += [{"index": {"_index": alias, "_id": doc["id"]}}, doc]
        response = await store.client.bulk(operations=operations, refresh="wait_for")
        failed = [item for item in response["items"] if item["index"].get("error")]
        print(f"{len(operations) // 2 - len(failed)} talks indexed into {alias}; {len(failed)} failed")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
