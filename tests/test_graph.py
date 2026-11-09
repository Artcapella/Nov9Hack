import json
from pathlib import Path

import numpy as np

from app.graph import GraphModel
from app.policy import SYNTHETIC, eligible, search_filters
from app.schemas import PeopleSearchRequest, ProfileCreate, SearchRequest
from conftest import participant, run
from scripts.generate_synthetic import generate


def seed_synthetic(application, count=6):
    store = application.state.store
    for index, record in enumerate(generate(count, seed=7)):
        run(store.create_profile(ProfileCreate(**record, consent_to_discover=True), seed_id=f"synth-{index}", dataset=SYNTHETIC))
    from app.worker import process_pending
    run(process_pending(store, concurrency=3))


def test_graph_requires_a_card_and_returns_3d_positions_and_neighbour_edges(application, client):
    client.post("/api/session")
    assert client.get("/api/graph").status_code == 403
    viewer, own = participant(application, "Graph Viewer", headline="ML engineer", can_help_with=["PyTorch", "RAG"])
    participant(application, "Graph Ops", headline="SRE", can_help_with=["Kubernetes", "Terraform"])
    participant(application, "Graph Sponsor", roles=["sponsor"], can_help_with=["Kubernetes"])
    seed_synthetic(application)
    data = viewer.get("/api/graph").json()
    names = {node["display_name"] for node in data["nodes"]}
    assert "Graph Sponsor" not in names and {"Graph Viewer", "Graph Ops"} <= names
    expected = sum(1 for card in generate(6, seed=7) if "recruiter" not in card["roles"])  # sponsors/recruiters hidden by default
    assert data["you"] == own["id"] and data["stats"]["synthetic"] == expected and data["stats"]["engine"] == "memory_concepts"
    assert all(set(node["position3"]) == {"x", "y", "z"} and "email" not in node and "owner_id" not in node for node in data["nodes"])
    ids = {node["id"] for node in data["nodes"]}
    assert data["edges"] and all(edge["a"] in ids and edge["b"] in ids and edge["a"] != edge["b"] for edge in data["edges"])
    hidden = viewer.get("/api/graph", params={"include_synthetic": "false", "include_sponsors": "true"}).json()
    assert hidden["stats"]["synthetic"] == 0 and {node["display_name"] for node in hidden["nodes"]} == {"Graph Viewer", "Graph Ops", "Graph Sponsor"}


def test_graph_recomputes_only_when_membership_changes(application):
    viewer, _ = participant(application, "Graph Viewer", can_help_with=["Python"])
    first = viewer.get("/api/graph").json()
    assert viewer.get("/api/graph").json()["stats"]["fingerprint"] == first["stats"]["fingerprint"]
    participant(application, "Graph Newcomer", can_help_with=["React"])
    assert viewer.get("/api/graph").json()["stats"]["count"] == first["stats"]["count"] + 1


def test_pca_axes_keep_their_sign_between_refreshes():
    model = GraphModel(store=None)
    rng = np.random.default_rng(1)
    base = rng.normal(size=(20, 8)) * np.array([5, 3, 2, 1, 1, 1, 1, 1])
    a, _ = model._project(base)
    b, explained = model._project(np.vstack([base, rng.normal(size=(1, 8))]))
    assert np.corrcoef(a[:, 0], b[:20, 0])[0, 1] > 0.9 and abs(sum(explained) - 1) < 1 and len(explained) == 3


def test_synthetic_filter_is_applied_to_every_search_surface(application):
    viewer, _ = participant(application, "Real Person", headline="Platform engineer", can_help_with=["Kubernetes", "observability"])
    participant(application, "Real Peer", headline="SRE", can_help_with=["Kubernetes", "observability"])
    seed_synthetic(application, 12)
    for path, body in (("/api/people/search", {"mode": "query", "question": "Kubernetes"}), ("/api/search", {"question": "Kubernetes observability"})):
        shown = viewer.post(path, json={**body, "include_synthetic": True}).json()
        hidden = viewer.post(path, json={**body, "include_synthetic": False}).json()
        shown, hidden = (shown["hits"], hidden["hits"]) if isinstance(shown, dict) else (shown, hidden)
        assert any(hit.get("dataset") == SYNTHETIC for hit in shown)
        assert hidden and all(hit.get("dataset") != SYNTHETIC for hit in hidden)
    filters = search_filters(SearchRequest(question="xyz", include_synthetic=False), "cnh-2026")
    assert {"bool": {"must_not": [{"term": {"dataset": SYNTHETIC}}]}} in filters
    doc = {"id": "synthetic-card", "event_id": "e", "consent_to_discover": True, "state": "indexed", "discoverable_until": "2999-01-01T00:00:00+00:00", "dataset": SYNTHETIC}
    assert eligible(doc, PeopleSearchRequest(include_synthetic=True), "e") and not eligible(doc, PeopleSearchRequest(include_synthetic=False), "e")


def test_attendees_cannot_set_the_dataset_tag(application):
    client, card = participant(application, "Sneaky", dataset=SYNTHETIC)
    assert card.get("dataset") is None


def test_api_rejects_introductions_to_synthetic_cards(application):
    viewer, _ = participant(application, "Real Person")
    seed_synthetic(application, 1)
    store = application.state.store
    synthetic = next(doc for doc in store.memory_profiles.values() if doc.get("dataset") == SYNTHETIC)
    response = viewer.post("/api/connection-requests", json={
        "requester_name": "Real Person", "recipient_profile_id": synthetic["id"], "message": "Hello there",
    })
    assert response.status_code == 400
    assert not store.memory["connections"]


def test_generated_cohort_is_deterministic_and_valid():
    cards = generate(40)
    assert cards == generate(40) and len({card["display_name"] for card in cards}) > 30
    for card in cards:
        ProfileCreate(**card, consent_to_discover=True)
    checked_in = json.loads(Path("data/synthetic_profiles.json").read_text(encoding="utf-8"))
    assert checked_in[:40] == cards


def test_links_exist_only_after_interaction_and_strength_follows_kind(application):
    viewer, own = participant(application, "Link Viewer", can_help_with=["Python"])
    other, theirs = participant(application, "Link Peer", can_help_with=["Rust"])
    third, _ = participant(application, "Link Bystander", can_help_with=["Go"])
    assert viewer.get("/api/graph").json()["edges"] == []
    assert viewer.post("/api/interactions", json={"target_id": theirs["id"], "kind": "view"}).status_code == 201
    mine = viewer.get("/api/graph").json()
    assert [(e["kind"], e["strength"]) for e in mine["edges"]] == [("view", 1.0)] and mine["activity"]
    assert third.get("/api/graph").json()["edges"] == []  # profile views are private to the pair
    viewer.post("/api/interactions", json={"target_id": theirs["id"], "kind": "meet"})
    # One person's claim is visible only to the pair, marked unconfirmed, until the other logs it too.
    assert third.get("/api/graph").json()["edges"] == []
    pending = viewer.get("/api/graph").json()["edges"][0]
    assert pending["kind"] == "meet" and pending["confirmed"] is False
    other.post("/api/interactions", json={"target_id": own["id"], "kind": "meet"})
    link = third.get("/api/graph").json()["edges"][0]
    assert link["kind"] == "meet" and link["strength"] >= 5 and link["kinds"] == {} and link["confirmed"] is True
    assert viewer.post("/api/interactions", json={"target_id": own["id"], "kind": "meet"}).status_code == 400
    assert viewer.post("/api/interactions", json={"target_id": theirs["id"], "kind": "hug"}).status_code == 422


def test_clusters_are_labelled_from_member_features(application):
    viewer, _ = participant(application, "Cluster Viewer", can_help_with=["Kubernetes"])
    seed_synthetic(application, 24)
    data = viewer.get("/api/graph").json()
    assert len(data["clusters"]) >= 2 and all(c["label"] and c["size"] for c in data["clusters"])
    assert {n["cluster"] for n in data["nodes"]} == {c["id"] for c in data["clusters"]}
    assert all(e.get("simulated") for e in data["edges"])  # only SynthDemo pairs have (simulated) links


def test_live_telemetry_reports_route_templates_only(application):
    viewer, _ = participant(application, "Telemetry Viewer")
    viewer.get("/api/graph")
    data = viewer.get("/api/telemetry/live").json()
    routes = {r["route"] for r in data["routes"]}
    assert "GET /api/graph" in routes and data["total"] >= 1 and len(data["series"]) == 24
