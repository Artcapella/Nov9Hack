"""Add the networking-break index and verify live discovery; never seed records.

Run with --apply to create the new alias. Without it this is read-only.
Prints aggregate verification only, never attendee data or credentials.
"""
import argparse
import asyncio
import json
import logging

from app.config import get_settings
from app.elastic_schema import mappings
from app.policy import visibility_filters
from app.store import NetworkStore


async def main(apply=False):
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Live Elasticsearch configuration is required")
    logging.getLogger("elastic_transport").setLevel(logging.CRITICAL)
    store = NetworkStore(settings)
    await store.start()
    try:
        alias = settings.index('networking_breaks')
        if not await store.client.indices.exists(index=alias):
            if not apply:
                raise SystemExit("Networking-break index missing; run with --apply")
            await store.client.indices.create(index=alias + '-000001', mappings=mappings(settings)['networking_breaks'],
                                               aliases={alias: {'is_write_index': True}})
        actual = await store.client.indices.get_mapping(index=alias)
        expected = mappings(settings)['networking_breaks']['properties']
        if not all(all(body['mappings']['properties'].get(k) == v for k, v in expected.items()) for body in actual.values()):
            raise SystemExit("Networking-break mapping differs from expected schema")
        filters = visibility_filters(settings.demo_event_id) + [
            {'bool': {'must_not': [{'term': {'dataset': 'SynthDemo'}}, {'terms': {'roles': ['sponsor', 'recruiter']}}]}}]
        result = await store.client.search(index=settings.index('user_vectors'), size=3, source=False,
            query={'function_score': {'query': {'bool': {'filter': filters}},
                   'random_score': {'seed': 53, 'field': 'id'}, 'boost_mode': 'replace'}})
        breaks = await store.client.count(index=alias, query={'term': {'event_id': settings.demo_event_id}})
        print(json.dumps({'live_elasticsearch': True, 'break_mapping_verified': True,
                          'random_discovery_query_verified': True, 'real_candidates_returned': len(result['hits']['hits']),
                          'stored_breaks': breaks['count'], 'attendee_records_created': 0}))
    except Exception:
        raise SystemExit('Live verification failed; check connectivity and index permissions') from None
    finally:
        await store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
