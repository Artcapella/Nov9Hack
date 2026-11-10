"""Explicit, expiring networking availability; no passive presence tracking."""
from datetime import UTC, datetime, timedelta

from elasticsearch import NotFoundError
from fastapi import HTTPException

from app.policy import SYNTHETIC, eligible, search_filters
from app.schemas import PublicProfile, SearchRequest


async def set_availability(store, profile, request):
    if profile.get("dataset") == SYNTHETIC:
        raise HTTPException(400, "Demo profiles cannot share live availability")
    until = min(datetime.now(UTC) + timedelta(minutes=request.minutes),
                datetime.fromisoformat(profile["discoverable_until"])) if request.enabled else None
    value = until.isoformat() if until else None
    if store.client:
        try:
            # Update only metadata: preserve embeddings and never upsert a withdrawn card.
            await store.client.update(index=store.settings.index("user_vectors"), id=profile["id"],
                                      doc={"available_until": value}, refresh="wait_for")
        except NotFoundError:
            raise HTTPException(410, "Your card is no longer discoverable") from None
    else:
        current = store.memory_profiles.get(profile["id"])
        if current:
            current["available_until"] = value
    if not await store.public_document(profile["id"]):
        raise HTTPException(410, "Your card is no longer discoverable")
    return {"available_until": value}


async def available_people(store, viewer):
    request = SearchRequest(question="available", available_now=True, include_synthetic=False)
    if store.client:
        response = await store.client.search(index=store.settings.index("user_vectors"), size=24,
            source=list(PublicProfile.model_fields) + ["event_id", "consent_to_discover", "state"],
            query={"bool": {"filter": search_filters(request, store.settings.demo_event_id, viewer["id"])}},
            sort=[{"available_until": "desc"}])
        docs = [hit["_source"] for hit in response["hits"]["hits"]]
    else:
        docs = sorted(store.memory_profiles.values(), key=lambda doc: doc.get("available_until") or "", reverse=True)
    hits = [store.public_profile(doc) for doc in docs if eligible(doc, request, store.settings.demo_event_id, viewer["id"])]
    return {"hits": hits[:24]}
