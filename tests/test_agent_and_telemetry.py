import json

import httpx
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent_tools import definitions
from app.config import Settings
from app.rag import answer_with_evidence
from app.telemetry import configure_telemetry, exporter_config
from conftest import participant, run


def agent_settings():
    return Settings(_env_file=None, agent_builder_enabled=True, kibana_url="https://elastic.example.com",
                    agent_builder_api_key="synthetic-test-key")


def test_converse_normalizes_response_verifies_tools_and_limits_capabilities(application, monkeypatch):
    _, _ = participant(application)
    settings = agent_settings()
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=next(tool for tool in definitions(settings) if request.url.path.endswith(tool["id"])))
        return httpx.Response(200, json={"response": {"message": "Python expertise is available [1]."},
            "steps": [{"type": "tool_call", "tool_id": "network.find_people", "params": {"question": "DO_NOT_RETURN_INTERNAL_TOOL_TEXT"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr("app.rag.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = run(answer_with_evidence("Python", application.state.store, settings))
    assert result["mode"] == "elastic_agent_builder" and result["tool_ids"] == ["network.find_people"]
    assert "DO_NOT_RETURN_INTERNAL_TOOL_TEXT" not in str(result)
    body = json.loads(calls[-1].content)
    assert body["agent_id"] == settings.agent_builder_agent_id
    assert body["configuration_overrides"] == {"enable_elastic_capabilities": False, "skill_ids": [],
                                               "tools": [{"tool_ids": ["network.find_people", "network.conference_skill_summary"]}]}
    assert "conversation_id" not in body  # One-shot calls cannot cross participant histories.
    assert calls[-1].headers["kbn-xsrf"] == "true"


def test_misconfigured_agent_scope_fails_closed_to_retrieval(application, monkeypatch, caplog):
    participant(application)
    def handler(request):
        return httpx.Response(200, json={"type": "esql", "configuration": {"query": "FROM private_contacts"}})
    original = httpx.AsyncClient
    monkeypatch.setattr("app.rag.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = run(answer_with_evidence("Python", application.state.store, agent_settings()))
    assert result["mode"] == "retrieval_only" and result["agent_status"] == "unavailable"
    assert "private_contacts" not in str(result) + caplog.text


def test_model_error_does_not_leak_payload_or_credentials(application, monkeypatch, caplog):
    participant(application)
    def handler(request):
        raise httpx.ConnectError("PRIVATE_USER_PROMPT_OR_API_KEY")
    original = httpx.AsyncClient
    monkeypatch.setattr("app.rag.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = run(answer_with_evidence("Python", application.state.store, agent_settings()))
    assert result["mode"] == "retrieval_only" and result["sources"]
    assert "PRIVATE_USER_PROMPT_OR_API_KEY" not in caplog.text + str(result)


def test_otlp_uses_signal_endpoints_and_dash0_authorization():
    settings = Settings(_env_file=None, dash0_otlp_http_base="https://ingest.example.com/", dash0_auth_token="synthetic-token", dash0_dataset="event")
    base, headers = exporter_config(settings)
    assert base + "/v1/traces" == "https://ingest.example.com/v1/traces"
    assert base + "/v1/metrics" == "https://ingest.example.com/v1/metrics"
    assert headers == {"Authorization": "Bearer synthetic-token", "Dash0-Dataset": "event"}
    legacy = settings.model_copy(update={"dash0_otlp_http_base": None, "dash0_auth_token": None,
        "otel_exporter_otlp_endpoint": "https://ingest.example.com/v1/traces", "otel_exporter_otlp_headers": "Authorization=Bearer%20encoded-token"})
    assert exporter_config(legacy) == ("https://ingest.example.com", {"Authorization": "Bearer encoded-token"})


def test_app_creation_defers_exporters_until_startup(monkeypatch):
    from fastapi.testclient import TestClient
    from unittest.mock import Mock
    from app.main import create_app

    configure, shutdown = Mock(), Mock()
    monkeypatch.setattr("app.main.configure_telemetry", configure)
    monkeypatch.setattr("app.main.shutdown_telemetry", shutdown)
    app = create_app(Settings(_env_file=None))
    configure.assert_not_called()
    with TestClient(app) as client:
        configure.assert_called_once_with(None, app.state.settings)
        assert client.get("/healthz").status_code == 200
    shutdown.assert_called_once()


def test_real_request_spans_exclude_text_contacts_cookies_and_token_urls(application):
    exporter = InMemorySpanExporter()
    tp, mp = configure_telemetry(application, application.state.settings)
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    requester, _ = participant(application, "Demo Requester", public_summary="PRIVATE_SUMMARY_MARKER", email="synthetic@example.com")
    recipient, card = participant(application, "Demo Recipient")
    created = requester.post("/api/connection-requests", json={"requester_name": "Requester", "recipient_profile_id": card["id"], "message": "PRIVATE_MESSAGE_MARKER"}).json()
    requester.post("/api/search", json={"question": "PRIVATE_QUESTION_MARKER"})
    recipient.post(f"/api/connection-requests/{created['token']}/accept")
    tp.force_flush()
    spans = exporter.get_finished_spans()
    assert any(span.name == "network.hybrid_search" for span in spans)
    serialized = "\n".join(span.to_json() for span in spans)
    for forbidden in ("PRIVATE_SUMMARY_MARKER", "PRIVATE_MESSAGE_MARKER", "PRIVATE_QUESTION_MARKER", "synthetic@example.com", created["token"], requester.cookies.get("network_session")):
        assert forbidden not in serialized
