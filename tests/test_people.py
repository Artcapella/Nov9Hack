import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.elastic_schema import mappings
from app.people import DISCIPLINES, RING, people_search, project, similarity_from_score
from app.policy import visibility_filters
from app.schemas import PeopleSearchRequest
from conftest import participant, run


def search(client, **body):
    response = client.post("/api/people/search", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_people_view_requires_an_indexed_card(client):
    client.post("/api/session")
    assert client.post("/api/people/search", json={"mode": "query"}).status_code == 403


def test_card_similarity_is_a_percentage_with_vector_space_colour(application):
    viewer, own = participant(application, "Demo Viewer", headline="Frontend developer", can_help_with=["React", "CSS"],
                              looking_for=["Kubernetes help"])
    _, web = participant(application, "Demo Web", headline="Frontend engineer", can_help_with=["React", "TypeScript"])
    _, ops = participant(application, "Demo Ops", headline="SRE", can_help_with=["Kubernetes", "Terraform"])
    _, sponsor = participant(application, "Demo Sponsor", roles=["sponsor"], can_help_with=["React"])
    result = search(viewer, mode="query")
    ids = [hit["id"] for hit in result["hits"]]
    assert result["basis"] == "card" and result["engine"] == "memory_demo" and "not Jina" in result["similarity"]
    assert own["id"] not in ids and sponsor["id"] not in ids
    assert ids[0] == web["id"] and ops["id"] in ids
    percents = [hit["percent"] for hit in result["hits"]]
    assert percents == sorted(percents, reverse=True) and all(0 <= p <= 100 for p in percents)
    first = result["hits"][0]
    assert first["discipline"] == "web" and {tag["label"] for tag in first["tags"]} == {"React", "TypeScript"}
    assert all(tag["discipline"] == "web" for tag in first["tags"])
    # Web sits at the web anchor's angle, which app.js renders as yellow.
    assert round(math.degrees(math.atan2(first["tags"][0]["position"]["y"], first["tags"][0]["position"]["x"]))) == 50
    assert "profile_semantic" not in first and "consent_to_discover" not in first
    assert result["colour_source"] == "keyword_affinity"


def test_modes_search_their_own_fields(application):
    viewer, _ = participant(application, "Demo Viewer", can_help_with=["React"], looking_for=["observability"],
                            interests=["chess", "bouldering"])
    _, helper = participant(application, "Demo Helper", can_help_with=["OpenTelemetry tracing"])
    _, climber = participant(application, "Demo Climber", can_help_with=["SQL"], interests=["bouldering"])
    _, recruiter = participant(application, "Demo Recruiter", roles=["recruiter"], looking_for=["React developers"])
    problem = search(viewer, mode="problem")
    assert [hit["id"] for hit in problem["hits"]] == [helper["id"]]
    assert problem["hits"][0]["explanation"]["fields_searched"] == ["can_help_with"]
    interests = search(viewer, mode="interests")
    assert [hit["id"] for hit in interests["hits"]] == [climber["id"]]
    assert interests["hits"][0]["reasons"] == ["Shared interests: bouldering"]
    assert interests["hits"][0]["tags"][0]["label"] == "bouldering"
    hire = search(viewer, mode="hire")
    assert [hit["id"] for hit in hire["hits"]] == [recruiter["id"]]
    typed = search(viewer, mode="query", question="tracing OpenTelemetry")
    assert typed["basis"] == "query" and typed["hits"][0]["id"] == helper["id"]


def test_missing_text_returns_a_note_not_everyone(application):
    viewer, _ = participant(application, "Demo Viewer")
    participant(application, "Demo Other", interests=["chess"])
    result = search(viewer, mode="interests")
    assert result["hits"] == [] and "interests" in result["note"]


def test_discover_orders_by_distance_from_your_cluster(application):
    viewer, _ = participant(application, "Demo Viewer", headline="Frontend", can_help_with=["React", "CSS"])
    _, near = participant(application, "Demo Near", headline="Frontend", can_help_with=["React", "accessibility"])
    _, far = participant(application, "Demo Far", headline="Security", can_help_with=["IAM", "threat modeling"])
    hits = search(viewer, mode="discover")["hits"]
    assert [hit["id"] for hit in hits] == [far["id"], near["id"]]
    assert hits[0]["novelty"]["new_areas"] == ["security"] and hits[0]["reasons"][0] == "Outside your cluster: security"


def test_filters_reuse_the_shared_policy(application):
    viewer, _ = participant(application, "Demo Viewer", can_help_with=["Python"])
    _, async_only = participant(application, "Demo Async", can_help_with=["Python"], formats=["async"])
    _, one = participant(application, "Demo One", can_help_with=["Python"], formats=["one_to_one"], availability="now")
    hits = search(viewer, mode="query", question="python", formats=["one_to_one"], available_now=True)["hits"]
    assert [hit["id"] for hit in hits] == [one["id"]]
    assert "formats in [one_to_one]" in hits[0]["explanation"]["policy_gates"]


def test_elastic_mode_uses_semantic_scores_and_jina_anchor_colour(application):
    _, own = participant(application, "Demo Viewer", can_help_with=["React"])
    _, peer = participant(application, "Demo Peer", headline="Frontend", can_help_with=["React"])
    store = application.state.store
    viewer, peer_doc = store.memory["user_vectors"][own["id"]], store.memory["user_vectors"][peer["id"]]
    web = [1.0] + [0.0] * (len(DISCIPLINES) - 1)
    anchors = {d["anchor"]: [float(i == j) for j in range(len(DISCIPLINES))] for i, d in enumerate(DISCIPLINES)}

    async def inference(inference_id, input):
        # Anchors embed on their own axis, anything mentioning React on the web axis, the rest is neutral.
        return {"text_embedding": [{"embedding": anchors.get(text, web if "React" in text else [1.0] * len(DISCIPLINES))} for text in input]}
    client = SimpleNamespace(search=AsyncMock(return_value={"hits": {"hits": [{"_source": peer_doc, "_score": 0.71}]}}),
                             inference=SimpleNamespace(inference=AsyncMock(side_effect=inference)))
    store.client = client
    request = PeopleSearchRequest(mode="problem", question="React perf")
    result = run(people_search(store, request, viewer))
    query = client.search.call_args.kwargs["query"]["bool"]
    assert query["must"] == [{"semantic": {"field": "offers_semantic", "query": "React perf"}}]
    assert query["filter"][:4] == visibility_filters("cnh-2026")
    hit = result["hits"][0]
    assert hit["percent"] == 42 and result["colour_source"] == "jina"
    assert hit["tags"][0]["discipline"] == "web" and hit["position"] == project((web, 1.0))["position"]
    # Cached: a second search embeds nothing new.
    calls = client.inference.inference.call_count
    run(people_search(store, request, viewer))
    assert client.inference.inference.call_count == calls


def test_inference_failure_falls_back_to_labeled_keyword_colour(application):
    _, own = participant(application, "Demo Viewer", can_help_with=["React"])
    _, peer = participant(application, "Demo Peer", can_help_with=["Kubernetes"])
    store = application.state.store
    store.client = SimpleNamespace(search=AsyncMock(return_value={"hits": {"hits": [{"_source": store.memory["user_vectors"][peer["id"]], "_score": 0.6}]}}),
                                   inference=SimpleNamespace(inference=AsyncMock(side_effect=RuntimeError("down"))))
    result = run(people_search(store, PeopleSearchRequest(question="infra"), store.memory["user_vectors"][own["id"]]))
    assert result["colour_source"] == "keyword_affinity" and result["hits"][0]["discipline"] == "systems"


def test_semantic_outage_is_a_503_not_a_lexical_substitute(application):
    _, own = participant(application, "Demo Viewer", can_help_with=["React"])
    store = application.state.store
    store.client = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("inference down")))
    with pytest.raises(HTTPException) as exc:
        run(people_search(store, PeopleSearchRequest(question="React"), store.memory["user_vectors"][own["id"]]))
    assert exc.value.status_code == 503 and store.client.search.call_count == 1


def test_score_and_projection_helpers():
    assert similarity_from_score(0.5) == 0 and similarity_from_score(1.0) == 1 and math.isclose(similarity_from_score(0.675), 0.35)
    assert similarity_from_score(None) == 0 and similarity_from_score(0.2) == 0
    assert project(([0.0] * 6, 1.0)) == {"position": {"x": 0.0, "y": 0.0}, "discipline": None}
    even = project(([1.0] * 6, 1.0))
    assert even["discipline"] is None and math.hypot(even["position"]["x"], even["position"]["y"]) < RING * 0.25


def test_affinity_cache_eviction_preserves_current_request(application, monkeypatch):
    space = application.state.store.space
    space.anchors = [[1.0, 0.0]] * len(DISCIPLINES)
    space.cache = {"cached": ([1.0] * len(DISCIPLINES), 0.5)}
    monkeypatch.setattr("app.people.CACHE_LIMIT", 1)
    space._embed = AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]])
    result = run(space._embedded({"cached", "new", "another"}))
    assert set(result) == {"cached", "new", "another"}
    assert result["cached"][1] == 0.5
    assert len(space.cache) <= 1


def test_ensure_indices_adds_missing_fields_and_rejects_changed_ones(application):
    store = application.state.store
    expected = {store.settings.index(name): mapping["properties"] for name, mapping in mappings(store.settings).items()}
    user_vectors = store.settings.index("user_vectors")
    old = {key: value for key, value in expected[user_vectors].items() if key not in {"interests", "interests_semantic", "offers_semantic"}}
    live = {**expected, user_vectors: old}
    indices = SimpleNamespace(exists=AsyncMock(return_value=True), exists_alias=AsyncMock(return_value=True), put_mapping=AsyncMock(),
                              get_mapping=AsyncMock(side_effect=lambda index: {f"{index}-000001": {"mappings": {"properties": live[index]}}}))
    store.client = SimpleNamespace(inference=SimpleNamespace(get=AsyncMock()), indices=indices)
    run(store.ensure_indices())
    indices.put_mapping.assert_called_once()
    assert indices.put_mapping.call_args.kwargs["index"] == user_vectors
    assert set(indices.put_mapping.call_args.kwargs["properties"]) == {"interests", "interests_semantic", "offers_semantic"}
    old["interests"] = {"type": "text"}
    with pytest.raises(RuntimeError, match="Incompatible schema"):
        run(store.ensure_indices())
