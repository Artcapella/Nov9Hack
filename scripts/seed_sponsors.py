"""Seed only four clearly labeled fictional booths, with stable non-attendee IDs."""
import asyncio

from app.config import get_settings
from app.policy import SYNTHETIC
from app.schemas import ProfileCreate
from app.sponsors import BOOTHS
from app.store import NetworkStore
from app.worker import publish_job


async def seed_sponsors(store):
    count = 0
    for booth in BOOTHS:
        profile = ProfileCreate(display_name=f"{booth['name']} Demo Booth", headline=f"Sponsor · {booth['focus']}",
            public_summary=f"Synthetic demo data. Fictional booth {booth['booth']}. {booth['description']} {booth['contribution']}",
            can_help_with=booth['topics'], looking_for=[booth['prompt']], roles=['sponsor'],
            formats=['async', 'one_to_one'], availability='Demo scenario; confirm with the event organizer',
            consent_to_discover=True)
        result = await store.create_profile(profile, seed_id=f"sponsor-booth-{booth['id']}", dataset=SYNTHETIC)
        job = await store.claim_job(result['job_id'])
        if job:
            try:
                state = await publish_job(store, job)
                await store.finish_job(job['job_id'], state, job['claim_id'])
            except Exception:
                await store.finish_job(job['job_id'], 'retry', job['claim_id'], 'publication_unavailable')
                raise
        if await store.public_document(result['id']):
            count += 1
    return count


async def main():
    store = NetworkStore(get_settings())
    await store.start()
    try:
        count = await seed_sponsors(store)
        print(f"Verified {count} discoverable SynthDemo sponsor booths. No real attendees changed.")
        if count != len(BOOTHS):
            raise SystemExit("Some sponsor jobs are still being published by another worker; rerun to verify all four.")
    finally:
        await store.close()


if __name__ == '__main__':
    asyncio.run(main())
