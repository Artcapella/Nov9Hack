"""General similarity view: rank people, score them as a percentage, and place them in one colour space.

Ranking and percentages: in Elastic mode a `semantic` query on a pinned Jina
semantic_text field. For dense cosine / normalised dot-product fields
Elasticsearch reports _score = (1 + cos) / 2, so the displayed percentage is the
recovered cosine, clamped to [0, 100]. The memory demo uses a transparent
concept + term cosine instead and says so; it is never presented as Jina.

Colour: text is compared with six discipline anchors on a ring. The weighted
sum of the anchor directions gives an (x, y) position. app.js:hueAt maps the
angle to a hue and the distance from the centre to saturation, and its CLUSTERS
use the same anchor angles, so cards, tags and the Field map agree. Generalist or
non-technical text lands near the grey centre. Elastic mode gets the anchor
weights from the same Jina endpoint (cached). Memory mode, and any inference
failure, falls back to keyword affinity, and the response labels which one was used.
"""
import logging
import math
import re
from collections import Counter

from fastapi import HTTPException

from app.insights import concepts_in, explain, profile_text, query_terms
from app.policy import describe_filters, eligible, search_filters
from app.schemas import PeopleSearchRequest, PublicProfile
from app.telemetry import fallback_counter, search_counter

logger = logging.getLogger(__name__)

RING = 0.62  # Same radius as app.js so server positions sit on the Field map's ring.
TEMPERATURE = 0.03  # Softmax sharpness over anchor cosines; Jina cosines differ by a few hundredths.
DISCOVER_WINDOW = 100
CACHE_LIMIT = 4096

# Angles (degrees) must match CLUSTERS[].hue in app/static/app.js.
DISCIPLINES = (
    {"label": "web", "angle": 50,
     "anchor": "Web development: frontend, React, TypeScript, JavaScript, CSS, HTML, accessibility, Node",
     "keywords": ("react", "typescript", "css", "accessibility", "frontend", "javascript", "web", "next.js", "ui", "html", "node")},
    {"label": "product · design", "angle": 112,
     "anchor": "Product and design: UX research, Figma, prototyping, product management, growth",
     "keywords": ("design", "ux", "figma", "product", "research", "prototyping", "growth")},
    {"label": "systems", "angle": 186,
     "anchor": "Systems and infrastructure: Kubernetes, cloud, DevOps, SRE, observability, networking, platform engineering",
     "keywords": ("kubernetes", "observability", "terraform", "sre", "infra", "devops", "cloud", "latency", "performance",
                  "opentelemetry", "platform", "docker", "networking", "k8s")},
    {"label": "data", "angle": 236,
     "anchor": "Data: SQL, analytics, data pipelines, ETL, Spark, data warehouses, Elasticsearch search",
     "keywords": ("sql", "spark", "analytics", "elasticsearch", "data", "pipelines", "etl", "search", "warehouse", "dbt")},
    {"label": "ai · ml", "angle": 288,
     "anchor": "AI and machine learning: LLMs, RAG, embeddings, NLP, PyTorch, MLOps, vector search, Python",
     "keywords": ("llm", "rag", "embeddings", "pytorch", "ml", "machine learning", "ai", "mlops", "nlp", "vector", "python")},
    {"label": "security", "angle": 350,
     "anchor": "Security and privacy: IAM, threat modeling, compliance, authentication, cryptography",
     "keywords": ("privacy", "iam", "threat", "compliance", "security", "auth", "crypto")},
)
NEUTRAL = ([0.0] * len(DISCIPLINES), 0.0)
_KEYWORDS = [[re.compile(rf"(?:^|[^a-z0-9]){re.escape(k)}(?:$|[^a-z0-9])") for k in d["keywords"]] for d in DISCIPLINES]

PROFILE_FIELDS = ["headline", "public_summary", "approved_experience", "can_help_with", "looking_for"]
MODES = {
    # semantic field searched in Elastic, public fields it was built from
    "query": ("profile_semantic", PROFILE_FIELDS),
    "problem": ("offers_semantic", ["can_help_with"]),
    "hire": ("profile_semantic", PROFILE_FIELDS),
    "interests": ("interests_semantic", ["interests"]),
    "discover": ("profile_semantic", PROFILE_FIELDS),
}
NEEDS_TEXT = {
    "query": "Type what you are looking for.",
    "problem": "Describe the problem, or add what you're looking for to your card.",
    "hire": "Type a role or skills, or add what you can help with to your card.",
    "interests": "Type an interest such as climbing or jazz. Your card has no interests outside work yet.",
    "discover": "Type a direction to explore.",
}


# ---- vector space -------------------------------------------------------------------------

def keyword_affinity(text: str) -> tuple[list[float], float]:
    lowered = (text or "").casefold()
    return [float(sum(1 for p in patterns if p.search(lowered))) for patterns in _KEYWORDS], 1.0


def embedding_affinity(vector: list[float], anchors: list[list[float]]) -> tuple[list[float], float]:
    sims = [cosine(vector, anchor) for anchor in anchors]
    top, mean = max(sims), sum(sims) / len(sims)
    # Text that is about equally (un)like every anchor stays near the grey centre.
    strength = max(0.0, min(1.0, (top - mean) / 0.08))
    return [math.exp((s - top) / TEMPERATURE) for s in sims], strength


def project(affinity: tuple[list[float], float]) -> dict:
    weights, strength = affinity
    total = sum(weights)
    if not total or not strength:
        return {"position": {"x": 0.0, "y": 0.0}, "discipline": None}
    x = sum(w / total * math.cos(math.radians(d["angle"])) for w, d in zip(weights, DISCIPLINES)) * RING * strength
    y = sum(w / total * math.sin(math.radians(d["angle"])) for w, d in zip(weights, DISCIPLINES)) * RING * strength
    discipline = DISCIPLINES[weights.index(max(weights))]["label"] if math.hypot(x, y) > RING * 0.25 else None
    return {"position": {"x": round(x, 4), "y": round(y, 4)}, "discipline": discipline}


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class VectorSpace:
    """Anchor-weight cache over the store's pinned inference endpoint."""

    def __init__(self, store):
        self.store = store
        self.anchors: list[list[float]] | None = None
        self.cache: dict[str, tuple[list[float], float]] = {}

    async def affinities(self, texts: set[str]) -> tuple[dict[str, tuple[list[float], float]], str]:
        texts = {text for text in texts if text}
        if self.store.client:
            try:
                return await self._embedded(texts), "jina"
            except Exception:
                logger.warning("Vector-space inference unavailable; colouring by keyword affinity")
                fallback_counter.add(1, {"operation": "vector_space"})
        return {text: keyword_affinity(text) for text in texts}, "keyword_affinity"

    async def _embedded(self, texts: set[str]) -> dict[str, tuple[list[float], float]]:
        if self.anchors is None:
            self.anchors = await self._embed([d["anchor"] for d in DISCIPLINES])
        missing = [text for text in texts if text not in self.cache]
        if missing:
            if len(self.cache) + len(missing) > CACHE_LIMIT:
                self.cache.clear()
            for text, vector in zip(missing, await self._embed(missing)):
                self.cache[text] = embedding_affinity(vector, self.anchors)
        return {text: self.cache[text] for text in texts}

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            response = await self.store.client.inference.inference(inference_id=self.store.settings.elastic_inference_id, input=batch)
            body = getattr(response, "body", response)
            rows = next(value for value in body.values() if isinstance(value, list))
            vectors.extend(row["embedding"] for row in rows)
            if len(vectors) != start + len(batch):
                raise ValueError("Inference returned a different number of embeddings")
        return vectors


# ---- memory demo similarity -----------------------------------------------------------------

def memory_vector(text: str) -> Counter:
    vector = Counter({f"c:{name}": 1.0 for name in concepts_in(text)})
    for term in query_terms(text):
        vector[f"t:{term}"] += 0.5
    return vector


def memory_similarity(a: str, b: str) -> float:
    va, vb = memory_vector(a), memory_vector(b)
    dot = sum(value * vb[key] for key, value in va.items())
    na, nb = math.sqrt(sum(v * v for v in va.values())), math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def similarity_from_score(score: float | None) -> float:
    return max(0.0, min(1.0, 2 * (score or 0.0) - 1))


# ---- texts per mode ---------------------------------------------------------------------------

def viewer_text(mode: str, viewer: dict) -> str:
    if mode == "problem":
        return ", ".join(viewer.get("looking_for", []))
    if mode == "interests":
        return ", ".join(viewer.get("interests", []))
    if mode == "hire":
        return " ".join([viewer.get("headline", ""), ", ".join(viewer.get("can_help_with", [])), viewer.get("approved_experience", "")]).strip()
    return profile_text(viewer)


def candidate_text(mode: str, profile: dict) -> str:
    if mode == "problem":
        return ", ".join(profile.get("can_help_with", []))
    if mode == "interests":
        return ", ".join(profile.get("interests", []))
    return profile_text(profile)


def position_text(profile: dict) -> str:
    """Where a person sits does not depend on the mode, so their colour is stable."""
    return f"{profile.get('headline', '')}. {', '.join(profile.get('can_help_with', []))}".strip(". ") or profile_text(profile)


def card_tags(mode: str, profile: dict) -> list[str]:
    primary = profile.get("interests", []) if mode == "interests" else profile.get("can_help_with", [])
    return list(dict.fromkeys(primary))[:4]


def reasons_for(mode: str, hit: dict, why: dict, viewer: dict, novelty: dict | None) -> list[str]:
    reasons = []
    if mode == "discover" and novelty and novelty["new_areas"]:
        reasons.append("Outside your cluster: " + ", ".join(novelty["new_areas"]))
    if mode == "interests":
        mine = {item.casefold() for item in viewer.get("interests", [])}
        shared = [item for item in hit.get("interests", []) if item.casefold() in mine]
        if shared:
            reasons.append("Shared interests: " + ", ".join(shared[:3]))
        # Work overlap is not why someone appears here.
        return reasons
    if mode == "hire" and hit.get("looking_for"):
        reasons.append("Hiring for: " + ", ".join(hit["looking_for"][:3]))
    if why.get("they_offer_you_need"):
        reasons.append("Offers what you need: " + ", ".join(why["they_offer_you_need"][:3]))
    if why.get("lexical_terms"):
        reasons.append("Shared terms: " + ", ".join(why["lexical_terms"][:3]))
    elif why.get("concepts"):
        reasons.append("Shared concepts: " + ", ".join(why["concepts"][:3]))
    if mode == "problem" and not why.get("they_offer_you_need") and hit.get("can_help_with"):
        reasons.append("Can help with: " + ", ".join(hit["can_help_with"][:3]))
    return reasons[:3]


def novelty_of(viewer_affinity, hit_affinity) -> dict:
    """Distance in discipline space, and which disciplines the person adds."""
    vw, hw = viewer_affinity[0], hit_affinity[0]
    vs, hs = sum(vw) or 1.0, sum(hw) or 1.0
    vshare, hshare = [w / vs for w in vw], [w / hs for w in hw]
    distance = 1 - cosine(vshare, hshare) if sum(vw) and sum(hw) else 0.5
    new = [d["label"] for d, v, h in zip(DISCIPLINES, vshare, hshare) if h >= 0.25 and v < 0.1]
    return {"distance": round(distance, 4), "new_areas": new}


# ---- search -------------------------------------------------------------------------------------

async def semantic_people(store, text: str, field: str, filters: list[dict], size: int) -> list[tuple[dict, float]]:
    source = list(PublicProfile.model_fields) + ["event_id", "consent_to_discover", "state", "profile_text"]
    try:
        response = await store.client.search(index=store.settings.index("user_vectors"), size=size, source=source,
                                             query={"bool": {"must": [{"semantic": {"field": field, "query": text}}], "filter": filters}})
    except Exception:
        # A lexical score cannot be shown as a vector similarity, so there is no lexical fallback here.
        logger.warning("Semantic people search unavailable")
        fallback_counter.add(1, {"operation": "people"})
        raise HTTPException(503, "Vector search is temporarily unavailable") from None
    return [(hit["_source"], similarity_from_score(hit.get("_score"))) for hit in response["hits"]["hits"]]


async def people_search(store, request: PeopleSearchRequest, viewer: dict) -> dict:
    mode, event_id = request.mode, store.settings.demo_event_id
    if mode == "hire":
        request = request.model_copy(update={"roles": ["recruiter", "sponsor"], "sponsor_intent": True})
    field, fields_searched = MODES[mode]
    typed = request.question.strip()
    text = typed or viewer_text(mode, viewer)
    engine = "elasticsearch" if store.client else "memory_demo"
    result = {"mode": mode, "engine": engine, "basis": "query" if typed else "card", "field": field, "hits": [], "note": None,
              "similarity": (f"Cosine similarity of {store.settings.elastic_inference_id} embeddings on {field}" if store.client
                             else "Memory demo: cosine over a transparent concept and term map, not Jina")}
    if not text:
        result["note"] = NEEDS_TEXT[mode]
        return result

    filters = search_filters(request, event_id, viewer["id"])
    if store.client:
        scored = await semantic_people(store, text, field, filters, DISCOVER_WINDOW if mode == "discover" else request.size * 2)
    else:
        scored = [(profile, memory_similarity(text, candidate_text(mode, profile)))
                  for profile in store.memory_profiles.values() if eligible(profile, request, event_id, viewer["id"])]
        scored = [item for item in scored if mode == "discover" or item[1] > 0]
    # Re-check every policy gate on what came back, as search_profiles does.
    scored = [item for item in scored if eligible(item[0], request, event_id, viewer["id"])]
    scored.sort(key=lambda item: -item[1])

    tags = {profile["id"]: card_tags(mode, profile) for profile, _ in scored}
    texts = {position_text(viewer)} | {position_text(profile) for profile, _ in scored} | {tag for items in tags.values() for tag in items}
    affinities, colour_source = await store.space.affinities(texts)
    def affinity(text):
        return affinities.get(text, NEUTRAL)
    viewer_affinity = affinity(position_text(viewer))

    novelty = {}
    if mode == "discover":
        for profile, _ in scored:
            novelty[profile["id"]] = novelty_of(viewer_affinity, affinity(position_text(profile)))
        scored.sort(key=lambda item: (-novelty[item[0]["id"]]["distance"], -item[1]))

    gates = describe_filters(search_filters(request, event_id, "<your card>"))
    for profile, similarity in scored[:request.size]:
        why = explain(profile, text, viewer, gates, ["semantic"])
        why["fields_searched"] = fields_searched
        pid = profile["id"]
        result["hits"].append({
            **store.public_profile(profile), "similarity": round(similarity, 4), "percent": round(similarity * 100),
            **project(affinity(position_text(profile))),
            "tags": [{"label": tag, **project(affinity(tag))} for tag in tags[pid]],
            "novelty": novelty.get(pid), "reasons": reasons_for(mode, profile, why, viewer, novelty.get(pid)), "explanation": why,
        })
    result["you"] = project(viewer_affinity)
    result["colour_source"] = colour_source
    search_counter.add(1, {"engine": engine, "mode": f"people_{mode}"})
    return result
