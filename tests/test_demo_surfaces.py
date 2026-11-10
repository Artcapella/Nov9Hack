from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.policy import describe_filters, search_filters
from app.schemas import SearchRequest
from conftest import participant, run


def test_compare_shows_conceptual_matches_keyword_search_misses(application):
    viewer, _ = participant(application, "Demo Viewer", looking_for=["observability mentor"], can_help_with=["React"])
    _, lexical = participant(application, "Demo Lexical", can_help_with=["Kubernetes latency"], looking_for=["frontend help"])
    _, conceptual = participant(application, "Demo Conceptual", can_help_with=["OpenTelemetry tracing"], public_summary="I chase bottlenecks.")
    result = viewer.post("/api/search/compare", json={"question": "Kubernetes app is slow"}).json()
    keyword_ids = [hit["id"] for hit in result["modes"]["keyword"]]
    hybrid_ids = [hit["id"] for hit in result["modes"]["hybrid"]]
    assert lexical["id"] in keyword_ids and conceptual["id"] not in keyword_ids
    assert {lexical["id"], conceptual["id"]} <= set(hybrid_ids)
    assert result["missed_by_keyword"] == [conceptual["id"]]
    assert result["engine"] == "memory_demo"
    hit = next(hit for hit in result["modes"]["hybrid"] if hit["id"] == lexical["id"])
    why = hit["explanation"]
    assert why["found_by"] == ["keyword", "semantic"] and "kubernetes" in why["lexical_terms"]
    assert "observability" in why["concepts"]
    assert why["you_offer_they_need"] == ["React"]
    assert "consent_to_discover == true" in why["policy_gates"]
    assert "NOT roles in [sponsor, recruiter]" in why["policy_gates"]


def test_semantic_comparison_never_substitutes_lexical(application):
    store = application.state.store
    search = AsyncMock(side_effect=RuntimeError("inference down"))
    store.client = SimpleNamespace(search=search)
    hits, mode = run(store._retrieve("user_vectors", "Python", [], "profile_text", "profile_semantic", 5, ["id"], "semantic"))
    assert hits == [] and mode == "semantic_unavailable" and search.call_count == 1


def test_describe_filters_renders_exact_policy():
    gates = describe_filters(search_filters(SearchRequest(question="Python", formats=["async"]), "cnh-2026", "me"))
    assert gates == ["event_id == cnh-2026", "consent_to_discover == true", "state == indexed", "discoverable_until > now",
                     "formats in [async]", "NOT roles in [sponsor, recruiter]", "NOT id == me"]


def test_pipeline_trust_and_withdrawal(application):
    client, card = participant(application, "Demo Owner", email="owner@example.com")
    pipeline = client.get("/api/me").json()["pipeline"]
    states = {stage["key"]: stage["state"] for stage in pipeline["stages"]}
    assert states == {"upload": "skipped", "extraction": "skipped", "restricted_vector": "skipped",
                      "approval": "done", "public_vector": "done", "evidence": "done"}
    assert pipeline["model_id"] == "memory demo · no inference"
    trust = client.get("/api/trust").json()
    assert trust["card"]["consent_to_discover"] is True
    stores = {store["label"]: store for store in trust["stores"]}
    assert stores["Contact details"]["present"] and stores["Public card"]["present"]
    assert "owner@example.com" not in str(trust)
    assert client.delete("/api/me").status_code == 204
    trust = client.get("/api/trust").json()
    assert trust["card"]["consent_to_discover"] is False
    assert not any(store["present"] for store in trust["stores"])
    assert {stage["state"] for stage in client.get("/api/me").json()["pipeline"]["stages"]} <= {"withdrawn", "skipped"}


def test_ops_summary_is_aggregate_only(application, client):
    owner, card = participant(application, "Demo Secret Name", can_help_with=["Python", "Kubernetes"], email="hidden@example.com")
    _, other = participant(application, "Demo Other", can_help_with=["Python"])
    assert owner.post("/api/connection-requests", json={"requester_name": "Requester", "recipient_profile_id": other["id"], "message": "SECRET_NOTE"}).status_code == 201
    summary = client.get("/api/ops/summary").json()
    text = str(summary)
    for secret in ("Demo Secret Name", card["id"], "hidden@example.com", "SECRET_NOTE"):
        assert secret not in text
    assert summary["consent"]["discoverable"] == 2
    assert summary["queue"]["by_state"] == {"indexed": 2}
    assert summary["connections"]["by_status"] == {"pending": 1}
    assert {"label": "python", "count": 2} in summary["impact"]["offers"]


def test_ops_summary_is_open_by_default_even_with_a_token():
    settings = Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None, ops_token="organizer-token-123456")
    with TestClient(create_app(settings, telemetry=False)) as client:
        assert client.get("/api/ops/summary").status_code == 200


def test_ops_summary_requires_token_when_locked():
    settings = Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None, ops_token="organizer-token-123456", ops_public=False)
    with TestClient(create_app(settings, telemetry=False)) as client:
        assert client.get("/api/ops/summary").status_code == 401
        assert client.get("/api/ops/summary", headers={"X-NETwork-Ops-Token": "wrong-token-1234567"}).status_code == 401
        assert client.get("/api/ops/summary", headers={"X-NETwork-Ops-Token": "organizer-token-123456"}).status_code == 200
