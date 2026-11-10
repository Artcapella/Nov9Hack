"""Talks and events that suit an attendee, ranked by Elastic.

The catalog is a fictional demo schedule (dataset "DemoSchedule") and the UI says so; a real event would
load its own agenda the same way (scripts.seed_talks). Each session is indexed with a Jina semantic_text
field and found with the concierge's pipeline: RRF over BM25 and semantic retrieval, then the Jina
cross-encoder reranker. With an empty search box the query is the attendee's own card (interests first,
then what they look for and offer), so suggestions start personalised. Nothing about the attendee is stored.
"""
import logging

from fastapi import HTTPException

from app.people import memory_similarity
from app.telemetry import search_counter

logger = logging.getLogger(__name__)

DATASET = "DemoSchedule"
SOURCE = ["id", "title", "kind", "track", "time", "room", "speaker", "abstract", "topics", "dataset", "event_id"]


def _talk(id, title, kind, track, time, room, abstract, topics):
    return {"id": id, "title": title, "kind": kind, "track": track, "time": time, "room": room,
            "speaker": "Demo speaker", "abstract": abstract, "topics": topics}


CATALOG = [
    _talk("hybrid-search-lab", "Hybrid search in practice: BM25, vectors and rerankers", "workshop", "Data & search", "10:00", "Room A",
          "Build a retriever that fuses keyword and semantic search, then reranks with a cross-encoder.",
          ["Elasticsearch", "hybrid search", "semantic search", "embeddings"]),
    _talk("rag-citations", "Shipping RAG that cites its sources", "talk", "AI · ML", "11:00", "Main hall",
          "Grounded answers, evaluation and agent tools for LLM features people can trust.", ["LLM", "RAG", "agents", "evaluation"]),
    _talk("k8s-pager", "Kubernetes without the pager fatigue", "talk", "Systems", "10:00", "Room B",
          "Deployment health, Helm hygiene and incident response for small platform teams.", ["Kubernetes", "SRE", "Helm", "incident response"]),
    _talk("otel-tracing", "Tracing a slow request end to end with OpenTelemetry", "workshop", "Systems", "13:00", "Room B",
          "Follow one request through traces, metrics and logs to find the bottleneck.", ["observability", "OpenTelemetry", "latency", "performance"]),
    _talk("accessible-react", "Accessible React components from day one", "workshop", "Web", "11:00", "Room C",
          "Keyboard support, screen readers and semantic HTML in a TypeScript component library.", ["React", "accessibility", "TypeScript", "frontend"]),
    _talk("design-critique", "Design critiques that actually help", "panel", "Product · design", "14:00", "Room C",
          "Running UX research readouts and Figma critiques that move a product forward.", ["UX research", "Figma", "prototyping", "product management"]),
    _talk("threat-modelling", "Threat modelling for small teams", "talk", "Security", "15:00", "Room A",
          "A one-hour threat model, least-privilege keys and authentication pitfalls.", ["security", "threat modeling", "IAM", "authentication"]),
    _talk("privacy-ml", "Privacy engineering for ML pipelines", "panel", "Security", "16:00", "Main hall",
          "Consent, retention and compliance when training data comes from people.", ["privacy", "MLOps", "compliance", "data governance"]),
    _talk("calm-pipelines", "Data pipelines that do not wake you up", "talk", "Data", "14:00", "Room A",
          "Testing dbt models, idempotent Spark jobs and analytics people can rely on.", ["data pipelines", "dbt", "Spark", "analytics"]),
    _talk("green-cloud", "Green software: measuring carbon in the cloud", "talk", "Systems", "15:00", "Room B",
          "Energy, emissions and cloud cost as engineering metrics.", ["sustainability", "climate", "cloud", "energy"]),
    _talk("mentor-rounds", "Mentorship speed rounds", "meetup", "Community", "12:30", "Atrium",
          "Ten-minute rounds with mentors on career growth, learning plans and feedback.", ["mentorship", "career growth", "learning"]),
    _talk("founders-lunch", "Builders' lunch: find a co-founder", "meetup", "Community", "12:30", "Lounge",
          "Meet people starting companies, looking for collaborators or a first team.", ["startups", "co-founder", "collaborators", "product strategy"]),
    _talk("hiring-fair", "Hiring hour: meet teams who are hiring", "meetup", "Careers", "16:00", "Expo floor",
          "Recruiters and hiring managers with open roles across engineering and design.", ["hiring", "recruiting", "careers", "job search"]),
    _talk("bouldering", "Morning bouldering meetup", "social", "Outside work", "07:30", "Lobby (shuttle to the gym)",
          "Climb before the keynote. All levels; shoes available at the gym.", ["climbing", "bouldering", "fitness"]),
    _talk("jazz-jam", "After-hours jazz jam", "social", "Outside work", "19:00", "Lounge",
          "Bring an instrument or just listen. Live music and conversation.", ["jazz", "music", "live music"]),
    _talk("games-night", "Chess and board games night", "social", "Outside work", "20:00", "Atrium",
          "Casual chess, strategy games and puzzles with other attendees.", ["chess", "board games", "strategy games"]),
]


def documents(event_id: str) -> list[dict]:
    docs = []
    for talk in CATALOG:
        text = f"{talk['title']}. {talk['abstract']} Topics: {', '.join(talk['topics'])}."
        docs.append({**talk, "event_id": event_id, "dataset": DATASET, "talk_text": text, "talk_semantic": text})
    return docs


def card_text(viewer: dict) -> str:
    return ", ".join(list(viewer.get("interests") or []) + list(viewer.get("looking_for") or []) + list(viewer.get("can_help_with") or []))


async def search_talks(store, viewer: dict, question: str, size: int) -> dict:
    typed = question.strip()
    text = typed or card_text(viewer)
    engine = "elasticsearch" if store.client else "memory_demo"
    result = {"engine": engine, "basis": "query" if typed else "card", "hits": [], "note": None, "retrieval_mode": None,
              "dataset": "Fictional demo schedule"}
    if not text:
        result["note"] = "Type a topic, or add interests to your card."
        return result
    event_id = store.settings.demo_event_id
    if store.client:
        try:
            hits, mode = await store._retrieve("talks", text, [{"term": {"event_id": event_id}}], "talk_text", "talk_semantic",
                                               size, SOURCE, rerank=True)
        except Exception:
            logger.warning("Talk search unavailable")
            raise HTTPException(503, "Talk search is temporarily unavailable") from None
    else:
        catalog = store.memory["talks"]
        if not catalog:
            catalog.update({doc["id"]: doc for doc in documents(event_id)})
        scored = sorted(((doc, memory_similarity(text, doc["talk_text"])) for doc in catalog.values() if doc.get("event_id") == event_id),
                        key=lambda item: -item[1])
        hits, mode = [{**doc, "score": score} for doc, score in scored if score > 0], "memory_demo"
    mine = {item.casefold() for item in card_text(viewer).split(", ") if item}
    result["retrieval_mode"] = mode
    result["hits"] = [{**{key: hit.get(key) for key in SOURCE if key != "event_id"},
                       "matches_card": [topic for topic in hit.get("topics") or [] if topic.casefold() in mine][:3]}
                      for hit in hits[:size] if hit.get("event_id") == event_id]
    search_counter.add(1, {"engine": engine, "mode": "talks"})
    return result
