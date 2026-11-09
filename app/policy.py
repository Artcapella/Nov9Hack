"""One visibility policy for discovery, evidence, lexical fallback, and MCP."""
from datetime import UTC, datetime

from app.schemas import SearchRequest

SYNTHETIC = "SynthDemo"  # dataset tag on administratively seeded synthetic cards


def visibility_filters(event_id: str) -> list[dict]:
    return [
        {"term": {"event_id": event_id}},
        {"term": {"consent_to_discover": True}},
        {"term": {"state": "indexed"}},
        {"range": {"discoverable_until": {"gt": "now"}}},
    ]


def visible(document: dict | None, event_id: str) -> bool:
    if not document or document.get("event_id") != event_id or document.get("consent_to_discover") is not True or document.get("state") != "indexed":
        return False
    try:
        expiry = datetime.fromisoformat(document["discoverable_until"])
        return expiry.tzinfo is not None and expiry > datetime.now(UTC)
    except (KeyError, ValueError, TypeError):
        return False


def search_filters(request: SearchRequest, event_id: str, exclude_id: str | None = None) -> list[dict]:
    filters = visibility_filters(event_id)
    if request.formats:
        filters.append({"terms": {"formats": request.formats}})
    if request.roles:
        filters.append({"terms": {"roles": request.roles}})
    if request.available_now:
        filters.append({"term": {"availability": "now"}})
    if not request.sponsor_intent:
        filters.append({"bool": {"must_not": [{"terms": {"roles": ["sponsor", "recruiter"]}}]}})
    if not getattr(request, "include_synthetic", True):
        filters.append({"bool": {"must_not": [{"term": {"dataset": SYNTHETIC}}]}})
    if exclude_id:
        filters.append({"bool": {"must_not": [{"term": {"id": exclude_id}}]}})
    return filters


def eligible(document: dict, request: SearchRequest, event_id: str, exclude_id: str | None = None) -> bool:
    return (visible(document, event_id)
            and document.get("id") != exclude_id
            and (not request.formats or bool(set(request.formats) & set(document.get("formats", []))))
            and (not request.roles or bool(set(request.roles) & set(document.get("roles", []))))
            and (not request.available_now or document.get("availability") == "now")
            and (request.sponsor_intent or not {"sponsor", "recruiter"}.intersection(document.get("roles", [])))
            and (getattr(request, "include_synthetic", True) or document.get("dataset") != SYNTHETIC))


def branch_queries(question: str, filters: list[dict], text_field: str, semantic_field: str) -> tuple[dict, dict]:
    """Lexical (BM25) and semantic_text branches; both carry every hard filter."""
    def branch(query):
        return {"bool": {"must": [query], "filter": filters}}
    return branch({"match": {text_field: question}}), branch({"match": {semantic_field: question}})


def describe_filters(filters: list[dict]) -> list[str]:
    """Human-readable rendering of the exact filters sent to Elasticsearch."""
    rendered = []
    for item in filters:
        if "term" in item:
            (field, value), = item["term"].items()
            rendered.append(f"{field} == {str(value).lower() if isinstance(value, bool) else value}")
        elif "terms" in item:
            (field, values), = item["terms"].items()
            rendered.append(f"{field} in [{', '.join(values)}]")
        elif "range" in item:
            (field, bounds), = item["range"].items()
            rendered.append(" AND ".join(f"{field} {'>' if op == 'gt' else '>=' if op == 'gte' else '<' if op == 'lt' else '<='} {value}"
                                         for op, value in bounds.items()))
        elif "bool" in item and "must_not" in item["bool"]:
            rendered.extend("NOT " + text for text in describe_filters(item["bool"]["must_not"]))
    return rendered


def hybrid_body(question: str, filters: list[dict], text_field: str, semantic_field: str, size: int, source: list[str]) -> tuple[dict, dict]:
    lexical, semantic = branch_queries(question, filters, text_field, semantic_field)
    return {
        "size": size, "_source": source,
        "retriever": {"rrf": {"rank_window_size": 50, "rank_constant": 20,
                              "retrievers": [{"standard": {"query": lexical}}, {"standard": {"query": semantic}}]}},
    }, lexical
