"""Interaction links: the network's edges exist only because two attendees interacted.

Strength grows with the kind of interaction: viewed a profile < attended the same session <
messaged (an introduction request or acceptance) < met in person. One document per
(actor, target, kind) keeps a count and the last time, so repeated views do not flood the index.

Privacy and integrity: profile views are shown only to the two people involved. A session,
message or meeting is one person's claim until the other person logs the same kind, so only
those two see it (`confirmed: false`); everyone else sees only the strongest confirmed kind.
Introductions record "messaged" in both directions on acceptance, never on request. Interactions
with withdrawn or hidden cards are dropped because the graph keeps only links whose endpoints are
visible nodes, and an explicit withdrawal deletes them (NetworkStore.erase_links).

The SynthDemo cohort has no real attendees to interact, so its links are simulated
deterministically from embedding neighbours and flagged `simulated`.
"""
import hashlib
from datetime import UTC, datetime

from fastapi import HTTPException

KINDS = {"view": 1, "event": 2, "message": 3, "meet": 5}
LABELS = {"view": "viewed profile", "event": "same session", "message": "messaged", "meet": "met in person"}
ACTIVITY_SECONDS = 120


def _now() -> str:
    return datetime.now(UTC).isoformat()


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


async def record(store, actor: dict, target_id: str, kind: str) -> dict:
    if kind not in KINDS:
        raise HTTPException(422, "Unknown interaction kind")
    if target_id == actor["id"]:
        raise HTTPException(400, "Choose another attendee")
    if not await store.public_document(target_id):
        raise HTTPException(404, "That card is unavailable")
    key = hashlib.sha256(f"{store.settings.demo_event_id}|{actor['id']}|{target_id}|{kind}".encode()).hexdigest()[:32]
    existing = await store.get("interactions", key)
    at = _now()
    doc = {"id": key, "event_id": store.settings.demo_event_id, "actor": actor["id"], "target": target_id, "kind": kind,
           "weight": KINDS[kind], "count": (existing or {}).get("count", 0) + 1,
           "first_at": (existing or {}).get("first_at", at), "last_at": at}
    await store.put("interactions", key, doc)
    store.recent_interactions[key] = doc  # read-your-writes before the index refreshes
    return {"kind": kind, "label": LABELS[kind], "count": doc["count"], "strength": KINDS[kind]}


async def _documents(store) -> list[dict]:
    docs = {doc["id"]: doc for doc in await store._event_docs("interactions", ["id", "actor", "target", "kind", "weight", "count", "first_at", "last_at", "event_id"], size=5000)}
    for key, doc in list(store.recent_interactions.items()):
        if doc["event_id"] == store.settings.demo_event_id and doc["last_at"] >= docs.get(key, {}).get("last_at", ""):
            docs[key] = doc
    if len(store.recent_interactions) > 2000:
        store.recent_interactions.clear()
    return list(docs.values())


async def links(store, viewer_id: str, node_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Aggregate interactions into one weighted link per pair, plus recent activity for flashing."""
    pairs: dict[tuple[str, str], dict] = {}
    activity = []
    cutoff = datetime.now(UTC).timestamp() - ACTIVITY_SECONDS
    documents = await _documents(store)
    directed = {(doc["actor"], doc["target"], doc["kind"]) for doc in documents}

    def confirmed(a, b, kind):
        return (a, b, kind) in directed and (b, a, kind) in directed

    for doc in documents:
        a, b = doc["actor"], doc["target"]
        if a not in node_ids or b not in node_ids:
            continue
        mine = viewer_id in (a, b)
        # Anyone can log a claim about anyone; others see it only once the other person logged the same kind.
        if not mine and (doc["kind"] == "view" or not confirmed(a, b, doc["kind"])):
            continue
        link = pairs.setdefault(pair_key(a, b), {"a": pair_key(a, b)[0], "b": pair_key(a, b)[1], "kind": doc["kind"], "count": 0,
                                                 "kinds": {}, "last_at": doc["last_at"], "mine": mine})
        link["count"] += doc.get("count", 1)
        link["kinds"][doc["kind"]] = link["kinds"].get(doc["kind"], 0) + doc.get("count", 1)
        if KINDS[doc["kind"]] > KINDS[link["kind"]]:
            link["kind"] = doc["kind"]
        link["last_at"] = max(link["last_at"], doc["last_at"])
        if datetime.fromisoformat(doc["last_at"]).timestamp() >= cutoff:
            activity.append({"a": a, "b": b, "kind": doc["kind"] if mine or doc["kind"] != "view" else "event",
                             "at": doc["last_at"], "key": f"{doc['id']}:{doc.get('count', 1)}"})
    out = []
    for link in pairs.values():
        # Strength: the strongest kind, plus a little for repetition and breadth.
        repeat = min(1.0, 0.15 * (link["count"] - 1)) + 0.25 * (len(link["kinds"]) - 1)
        # Views are private to the pair and need no confirmation; every other kind is mutual or pending.
        link["confirmed"] = link["kind"] == "view" or confirmed(link["a"], link["b"], link["kind"])
        if not link["mine"]:
            link["kinds"] = {}  # others see only the strongest kind
        out.append({**link, "strength": round(KINDS[link["kind"]] + repeat, 2), "label": LABELS[link["kind"]]})
    activity.sort(key=lambda item: item["at"], reverse=True)
    return out, activity[:40]


def simulated_links(docs: list[dict], similarity, synthetic: str, per_node: int = 2) -> list[dict]:
    """Deterministic stand-in interactions among SynthDemo cards: their nearest neighbours,
    with the kind chosen by a stable hash so the cohort shows every strength."""
    order = ["view", "event", "event", "message", "message", "meet"]
    idx = [i for i, doc in enumerate(docs) if doc.get("dataset") == synthetic]
    out, seen = [], set()
    for i in idx:
        ranked = [j for j in similarity[i].argsort()[::-1] if j != i and docs[j].get("dataset") == synthetic][:per_node]
        for j in ranked:
            a, b = pair_key(docs[i]["id"], docs[int(j)]["id"])
            if (a, b) in seen:
                continue
            seen.add((a, b))
            kind = order[int(hashlib.sha256(f"{a}|{b}".encode()).hexdigest(), 16) % len(order)]
            if kind == "view":
                continue  # views are private; simulated ones are not shown either
            out.append({"a": a, "b": b, "kind": kind, "count": 1, "kinds": {}, "last_at": None, "mine": False,
                        "strength": float(KINDS[kind]), "label": LABELS[kind], "simulated": True})
    return out
