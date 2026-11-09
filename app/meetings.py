"""Explicit attendee availability. An overlap is a suggestion, never a booking."""
from datetime import UTC, datetime

from fastapi import HTTPException

from app.policy import SYNTHETIC


async def current_break(store, profile):
    if profile.get("dataset") == SYNTHETIC:
        return None
    doc = await store.get("networking_breaks", profile["id"])
    if (not doc or doc["event_id"] != store.settings.demo_event_id
            or datetime.fromisoformat(doc["ends_at"]) <= datetime.now(UTC)):
        return None
    return {key: doc[key] for key in ("starts_at", "ends_at", "area")}


async def save_break(store, profile, request):
    if profile.get("dataset") == SYNTHETIC:
        raise HTTPException(400, "Only real attendees can share a networking break")
    if request.ends_at <= datetime.now(UTC):
        raise HTTPException(422, "Choose a future break")
    if request.ends_at > datetime.fromisoformat(profile["discoverable_until"]):
        raise HTTPException(422, "The break must end before your card expires")
    doc = {**request.model_dump(mode="json"), "profile_id": profile["id"], "event_id": store.settings.demo_event_id}
    await store.put("networking_breaks", profile["id"], doc)
    # Withdrawal can race with a save; never leave the late write behind.
    if not await store.public_document(profile["id"]):
        await store.delete("networking_breaks", profile["id"])
        raise HTTPException(410, "Your card is no longer discoverable")
    return {key: doc[key] for key in ("starts_at", "ends_at", "area")}


async def overlap(store, viewer, target_id):
    target = await store.public_document(target_id)
    if not target or target.get("dataset") == SYNTHETIC or target_id == viewer["id"]:
        raise HTTPException(404, "Choose another discoverable attendee")
    mine, theirs = await current_break(store, viewer), await current_break(store, target)
    if not mine or not theirs:
        return {"status": "unknown", "message": "Both people need to share a networking break."}
    start = max(datetime.now(UTC), datetime.fromisoformat(mine["starts_at"]), datetime.fromisoformat(theirs["starts_at"]))
    end = min(datetime.fromisoformat(mine["ends_at"]), datetime.fromisoformat(theirs["ends_at"]))
    if (end - start).total_seconds() < 300:
        return {"status": "no_overlap", "message": "No shared break of at least five minutes."}
    return {"status": "suggested", "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "your_area": mine["area"], "their_area": theirs["area"],
            "same_area": mine["area"].casefold() == theirs["area"].casefold(),
            "message": "Suggested time only. Confirm the place and travel time together."}
