from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.people import mutual_people
from app.policy import search_filters
from app.schemas import PeopleSearchRequest
from conftest import participant, run


def test_mutual_fit_needs_both_directions_in_memory_mode(application):
    client, _ = participant(application, "Demo Me", can_help_with=["React", "accessibility"], looking_for=["Kubernetes", "observability"])
    _, two_way = participant(application, "Demo Two Way", can_help_with=["Kubernetes", "observability"], looking_for=["React", "accessibility"])
    _, one_way = participant(application, "Demo One Way", can_help_with=["Kubernetes", "observability"], looking_for=["sales"])
    body = client.post("/api/people/search", json={"mode": "mutual", "question": "ignored"}).json()
    ids = [hit["id"] for hit in body["hits"]]
    assert ids[0] == two_way["id"] and one_way["id"] not in ids
    assert body["basis"] == "card" and body["hits"][0]["reasons"][0].startswith("They can help you")
    lonely, _ = participant(application, "Demo Lonely")
    assert lonely.post("/api/people/search", json={"mode": "mutual"}).json()["note"].startswith("Add both")


def test_mutual_fit_queries_elastic_with_stored_vectors_and_ranks_by_geometric_mean(application):
    _, me = participant(application, "Demo Me", can_help_with=["React"], looking_for=["Kubernetes"])
    _, one = participant(application, "Demo One Sided")
    _, two = participant(application, "Demo Two Way")
    store = application.state.store
    docs = {card["id"]: dict(store.memory_profiles[card["id"]]) for card in (me, one, two)}

    def with_vectors(pid, offers, needs):
        return {"_score": 1.0, "_source": {**docs[pid], "_inference_fields": {
            "offers_semantic": {"inference": {"chunks": {"offers_semantic": [{"embeddings": offers}]}}},
            "seeking_semantic": {"inference": {"chunks": {"seeking_semantic": [{"embeddings": needs}]}}}}}}

    mine = {"hits": {"hits": [with_vectors(me["id"], [0.0, 1.0], [1.0, 0.0])]}}
    # Elastic's linear fusion may rank a one-sided match first; the geometric mean must drop it.
    candidates = {"hits": {"hits": [with_vectors(one["id"], [1.0, 0.0], [1.0, 0.0]), with_vectors(two["id"], [0.8, 0.6], [0.6, 0.8])]}}
    search = AsyncMock(side_effect=[mine, candidates])
    store.client = SimpleNamespace(search=search)
    request = PeopleSearchRequest(mode="mutual")
    result = run(mutual_people(store, request, docs[me["id"]]))
    assert [hit["id"] for hit in result["hits"]] == [two["id"]] and result["hits"][0]["percent"] == 80
    assert all("_inference_fields" not in hit for hit in result["hits"])
    knns = [item["retriever"]["standard"]["query"]["knn"] for item in search.call_args_list[1].kwargs["retriever"]["linear"]["retrievers"]]
    # Your needs search their offers and your offers search their needs, with your own stored vectors.
    assert [(knn["field"], knn["query_vector"]) for knn in knns] == [("offers_semantic", [1.0, 0.0]), ("seeking_semantic", [0.0, 1.0])]
    assert all(knn["filter"] == search_filters(request, "cnh-2026", me["id"]) for knn in knns)


def test_anchor_embedding_batches_stay_within_the_inference_limit(application):
    store = application.state.store

    async def inference(inference_id, input):
        assert len(input) <= 16, "Elastic rejects more than 16 inputs per call"
        return {"text_embedding": [{"embedding": [1.0, 0.0]} for _ in input]}
    store.client = SimpleNamespace(inference=SimpleNamespace(inference=AsyncMock(side_effect=inference)))
    assert len(run(store.space._embed([f"text {i}" for i in range(40)]))) == 40
    assert store.client.inference.inference.await_count == 3
