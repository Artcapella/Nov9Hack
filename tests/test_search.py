import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.policy import hybrid_body, search_filters, visibility_filters
from app.schemas import SearchRequest
from conftest import participant, run


def test_memory_search_rag_and_preferences_have_visibility_parity(application):
    client, own = participant(application, "Demo Searcher")
    person, peer = participant(application, "Demo Peer", formats=["one_to_one"], availability="now")
    assert person.post('/api/me/availability', json={'enabled': True}).status_code == 200
    _, sponsor = participant(application, "Demo Sponsor", roles=["sponsor"])
    store = application.state.store
    public = store.memory["user_vectors"]
    evidence = store.memory["knowledge"]
    for suffix, changes in (("other-event", {"event_id": "another-event"}),
                            ("expired", {"discoverable_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}),
                            ("no-consent", {"consent_to_discover": False}),
                            ("revoked", {"state": "revoked"})):
        public[suffix] = {**copy.deepcopy(public[peer["id"]]), "id": suffix, **changes}
        evidence[suffix] = {**copy.deepcopy(evidence[f"profile-{peer['id']}"]), "id": suffix, "profile_id": suffix, **changes}
    # Legacy knowledge without policy metadata also fails closed.
    evidence["legacy"] = {"id": "legacy", "title": "Old", "content": "Python", "profile_id": peer["id"]}
    hits = client.post("/api/search", json={"question": "Python", "formats": ["one_to_one"], "available_now": True}).json()
    assert [hit["id"] for hit in hits] == [peer["id"]]
    assert hits[0]["retrieval_mode"] == "memory_demo"
    assert "profile_semantic" not in hits[0] and "consent_to_discover" not in hits[0]
    sources = client.post("/api/ask", json={"question": "Python"}).json()["sources"]
    assert {source["profile_id"] for source in sources} == {own["id"], peer["id"]}
    sponsored = client.post("/api/search", json={"question": "Python", "roles": ["sponsor"], "sponsor_intent": True}).json()
    assert [hit["id"] for hit in sponsored] == [sponsor["id"]]


def test_each_rrf_branch_and_fallback_share_all_hard_filters(application, caplog):
    store = application.state.store
    request = SearchRequest(question="Python", roles=["mentor"], formats=["async"], available_now=True)
    filters = search_filters(request, "cnh-2026", "self")
    body, lexical = hybrid_body(request.question, filters, "profile_text", "profile_semantic", 5, ["id"])
    for branch in body["retriever"]["rrf"]["retrievers"]:
        assert branch["standard"]["query"]["bool"]["filter"] == filters
    search = AsyncMock(side_effect=[RuntimeError("SENSITIVE_QUERY_OR_TOKEN"), {"hits": {"hits": []}}])
    store.client = SimpleNamespace(search=search)
    hits, mode = run(store._retrieve("user_vectors", request.question, filters, "profile_text", "profile_semantic", 5, ["id"]))
    assert hits == [] and mode == "lexical_fallback"
    assert search.call_args_list[1].kwargs["query"] == lexical
    assert "SENSITIVE_QUERY_OR_TOKEN" not in caplog.text
    assert visibility_filters("cnh-2026") == filters[:4]


def test_evidence_filters_apply_in_rrf_and_lexical_failure_does_not_use_memory(application):
    store = application.state.store
    store.memory["knowledge"]["stale"] = {"content": "Must never become a fallback"}
    search = AsyncMock(side_effect=[RuntimeError("reranker unavailable"), RuntimeError("semantic unavailable"), RuntimeError("lexical unavailable")])
    store.client = SimpleNamespace(search=search)
    with pytest.raises(RuntimeError, match="lexical unavailable"):
        run(store.retrieve_knowledge("Python"))
    reranked, plain = (call.kwargs["body"]["retriever"] for call in search.call_args_list[:2])
    assert reranked["text_similarity_reranker"]["retriever"] == plain
    for branch in plain["rrf"]["retrievers"]:
        assert branch["standard"]["query"]["bool"]["filter"] == visibility_filters("cnh-2026")
    assert search.call_args_list[2].kwargs["query"]["bool"]["filter"] == visibility_filters("cnh-2026")


def test_concierge_evidence_is_reranked_but_people_search_stays_rrf(application):
    store = application.state.store
    search = AsyncMock(return_value={"hits": {"hits": [{"_source": {"id": "a"}, "_score": 1.3}]}})
    store.client = SimpleNamespace(search=search)
    filters = visibility_filters("cnh-2026")
    hits, mode = run(store._retrieve("knowledge", "Python", filters, "content", "content_semantic", 5, ["id"], rerank=True))
    assert mode == "hybrid_reranked" and hits == [{"id": "a", "score": 1.3}]
    reranker = search.call_args.kwargs["body"]["retriever"]["text_similarity_reranker"]
    assert (reranker["inference_id"], reranker["field"], reranker["inference_text"]) == (".jina-reranker-v3", "content", "Python")
    assert reranker["rank_window_size"] >= search.call_args.kwargs["body"]["size"]
    for branch in reranker["retriever"]["rrf"]["retrievers"]:
        assert branch["standard"]["query"]["bool"]["filter"] == filters
    # People search and the compare view's hybrid column keep plain RRF.
    _, mode = run(store._retrieve("user_vectors", "Python", filters, "profile_text", "profile_semantic", 5, ["id"]))
    assert mode == "hybrid" and "rrf" in search.call_args.kwargs["body"]["retriever"]
    store.settings = store.settings.model_copy(update={"elastic_rerank_inference_id": None})
    _, mode = run(store._retrieve("knowledge", "Python", filters, "content", "content_semantic", 5, ["id"], rerank=True))
    assert mode == "hybrid"


def test_stale_evidence_rechecks_current_card(application):
    client, _ = participant(application, "Demo Searcher")
    _, card = participant(application, "Demo Peer")
    # A stale evidence write cannot bypass current card revocation.
    application.state.store.memory["user_vectors"][card["id"]]["consent_to_discover"] = False
    result = client.post("/api/ask", json={"question": "Python"}).json()
    assert card["id"] not in {source["profile_id"] for source in result["sources"]}
