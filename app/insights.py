"""Explainability and aggregate views built only from approved public fields.

The concept map is a transparent, deterministic vocabulary. In Elastic mode it
only *explains* hits that Jina/semantic_text already retrieved; in memory demo
mode it is also the labeled stand-in for semantic retrieval.
"""
import re
from collections import Counter

CONCEPTS: dict[str, tuple[str, ...]] = {
    "accessibility": ("accessibility", "accessible", "a11y", "inclusive", "screen reader", "wcag", "assistive"),
    "climate": ("climate", "sustainability", "sustainable", "carbon", "energy", "emissions", "green"),
    "observability": ("observability", "opentelemetry", "tracing", "traces", "latency", "monitoring", "bottleneck", "slow", "performance", "metrics", "incident"),
    "infrastructure": ("kubernetes", "k8s", "terraform", "cloud", "devops", "sre", "platform", "docker", "infra", "distributed systems"),
    "ai": ("llm", "rag", "embeddings", "machine learning", "ml", "ai", "nlp", "agent", "agents", "pytorch", "mlops", "model"),
    "search": ("search", "elasticsearch", "retrieval", "ranking", "semantic", "vector", "bm25"),
    "data": ("sql", "spark", "analytics", "data", "pipelines", "etl", "warehouse", "dbt", "pandas"),
    "web": ("react", "typescript", "frontend", "javascript", "web", "css", "ui", "html", "next.js"),
    "design": ("design", "ux", "figma", "prototyping", "user research", "product"),
    "security": ("security", "privacy", "iam", "threat", "compliance", "auth", "crypto"),
    "mentorship": ("mentor", "mentorship", "mentoring", "coaching", "learn", "learning", "guidance", "feedback"),
    "collaboration": ("collaborator", "collaborators", "cofounder", "co-founder", "partner", "building", "build", "team"),
}
_PATTERNS = {name: [re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])") for word in words] for name, words in CONCEPTS.items()}
STOPWORDS = {"about", "with", "that", "this", "from", "have", "want", "need", "people", "someone", "anyone", "find",
             "meet", "should", "could", "would", "their", "them", "they", "what", "when", "where", "which", "who",
             "help", "into", "just", "only", "like", "some", "after", "before", "three", "work", "working", "looking"}
PUBLIC_FIELDS = ["headline", "public_summary", "approved_experience", "can_help_with", "looking_for"]


def concepts_in(text: str) -> set[str]:
    lowered = (text or "").casefold()
    return {name for name, patterns in _PATTERNS.items() if any(p.search(lowered) for p in patterns)}


def query_terms(question: str) -> list[str]:
    seen = []
    for term in re.findall(r"[a-z0-9][a-z0-9.+#-]*", question.casefold()):
        if len(term) > 3 and term not in STOPWORDS and term not in seen:
            seen.append(term)
    return seen


def profile_text(profile: dict) -> str:
    return profile.get("profile_text") or " ".join(
        [profile.get(key, "") for key in ("headline", "public_summary", "approved_experience")]
        + list(profile.get("can_help_with", [])) + list(profile.get("looking_for", [])))


def memory_scores(question: str, profile: dict) -> tuple[float, float]:
    """(keyword, concept) scores for the in-memory demo. Not Jina; labeled as such."""
    text = profile_text(profile).casefold()
    keyword = float(sum(term in text for term in query_terms(question)))
    concept = float(len(concepts_in(question) & concepts_in(text)))
    return keyword, concept


def rrf(*rankings: list[str], k: int = 20) -> list[str]:
    scores: Counter = Counter()
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] += 1 / (k + rank + 1)
    return [key for key, _ in scores.most_common()]


def explain(hit: dict, question: str, viewer: dict | None, gates: list[str], found_by: list[str]) -> dict:
    text = profile_text(hit)
    lowered = text.casefold()
    lexical = [term for term in query_terms(question) if term in lowered][:6]
    shared_concepts = sorted(concepts_in(question) & concepts_in(text))
    explanation = {"found_by": found_by, "lexical_terms": lexical, "concepts": shared_concepts,
                   "they_offer_you_need": [], "you_offer_they_need": [], "shared_areas": [],
                   "fields_searched": PUBLIC_FIELDS, "policy_gates": gates}
    if viewer:
        def overlap(offers, needs):
            needs_text = " ".join(needs).casefold()
            needs_concepts = concepts_in(needs_text)
            return [item for item in offers if item.casefold() in needs_text or concepts_in(item) & needs_concepts][:4]
        explanation["they_offer_you_need"] = overlap(hit.get("can_help_with", []), viewer.get("looking_for", []))
        explanation["you_offer_they_need"] = overlap(viewer.get("can_help_with", []), hit.get("looking_for", []))
        explanation["shared_areas"] = sorted(concepts_in(profile_text(viewer)) & concepts_in(text))[:5]
    return explanation


def top(counter: Counter, limit: int = 8) -> list[dict]:
    return [{"label": label, "count": count} for label, count in counter.most_common(limit)]


def topic_summary(profiles: list[dict]) -> dict:
    """Aggregate topics from already-public card fields; no names or ids leave this function."""
    offers, seeks, roles, clusters = Counter(), Counter(), Counter(), Counter()
    for profile in profiles:
        offers.update(item.casefold() for item in profile.get("can_help_with", []))
        seeks.update(item.casefold() for item in profile.get("looking_for", []))
        roles.update(profile.get("roles", []))
        clusters.update(concepts_in(profile_text(profile)))
    return {"offers": top(offers), "seeks": top(seeks), "roles": top(roles), "clusters": top(clusters)}
