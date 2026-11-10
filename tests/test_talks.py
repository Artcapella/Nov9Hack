from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.talks import search_talks
from conftest import participant, run


def test_talks_follow_card_interests_or_typed_topic(application):
    client, _ = participant(application, "Demo Climber", interests=["climbing"])
    body = client.post("/api/talks/search", json={}).json()
    assert body["basis"] == "card" and body["hits"][0]["id"] == "bouldering"
    assert body["hits"][0]["matches_card"] == ["climbing"] and body["dataset"] == "Fictional demo schedule"
    typed = client.post("/api/talks/search", json={"question": "Kubernetes"}).json()
    assert typed["basis"] == "query" and typed["hits"][0]["id"] == "k8s-pager"


def test_talks_require_a_card(application):
    from fastapi.testclient import TestClient
    anonymous = TestClient(application, headers={"X-NETwork-Request": "1"})
    assert anonymous.post("/api/session").status_code == 200
    assert anonymous.post("/api/talks/search", json={}).status_code == 403


def test_talks_use_elastic_hybrid_retrieval_with_reranker(application):
    store = application.state.store
    hit = {"_source": {"id": "bouldering", "event_id": "cnh-2026", "title": "Morning bouldering meetup", "topics": ["climbing"]}, "_score": 1.2}
    search = AsyncMock(return_value={"hits": {"hits": [hit]}})
    store.client = SimpleNamespace(search=search)
    result = run(search_talks(store, {"interests": ["climbing"]}, "", 6))
    assert [h["id"] for h in result["hits"]] == ["bouldering"] and result["retrieval_mode"] == "hybrid_reranked"
    call = search.call_args.kwargs
    assert call["index"] == store.settings.index("talks")
    reranker = call["body"]["retriever"]["text_similarity_reranker"]
    assert reranker["field"] == "talk_text" and reranker["inference_text"] == "climbing"
    for branch in reranker["retriever"]["rrf"]["retrievers"]:
        assert branch["standard"]["query"]["bool"]["filter"] == [{"term": {"event_id": "cnh-2026"}}]
