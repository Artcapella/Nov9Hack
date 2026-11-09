"""Abuse budgets, mutual-consent links, and erasure on withdrawal."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.ratelimit import RateLimiter
from tests.conftest import participant


def test_token_bucket_refuses_after_burst_and_reports_retry_after():
    limiter = RateLimiter()
    assert all(limiter.take("k", 60, 3) == 0 for _ in range(3))
    wait = limiter.take("k", 60, 3)
    assert 0 < wait <= 1
    assert limiter.take("other", 60, 3) == 0  # budgets are per key


def test_costly_routes_are_budgeted_per_session(application):
    client, _ = participant(application, "Budget Asker")
    codes = [client.post("/api/ask", json={"question": "who knows python"}).status_code for _ in range(8)]
    assert codes[:6] == [200] * 6 and codes[-1] == 429
    response = client.post("/api/ask", json={"question": "who knows python"})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) >= 1
    # Another attendee has their own budget.
    other, _ = participant(application, "Budget Other")
    assert other.post("/api/ask", json={"question": "who knows python"}).status_code == 200


def test_session_creation_is_budgeted_per_client_ip(application):
    statuses = []
    for _ in range(70):
        with TestClient(application, headers={"X-NETwork-Request": "1"}) as fresh:
            statuses.append(fresh.post("/api/session").status_code)
    assert statuses[:60] == [200] * 60 and 429 in statuses[60:]


def test_trusted_client_ip_header_separates_clients():
    app = create_app(Settings(_env_file=None, client_ip_header="cf-connecting-ip"), telemetry=False)
    for ip in ("203.0.113.1", "203.0.113.2"):
        statuses = []
        for _ in range(61):
            with TestClient(app, headers={"X-NETwork-Request": "1", "cf-connecting-ip": ip}) as fresh:
                statuses.append(fresh.post("/api/session").status_code)
        assert statuses[:60] == [200] * 60 and statuses[60] == 429


def test_rate_limits_can_be_disabled_for_load_tests():
    app = create_app(Settings(_env_file=None, rate_limits_enabled=False), telemetry=False)
    with TestClient(app, headers={"X-NETwork-Request": "1"}) as client:
        assert all(client.post("/api/session").status_code == 200 for _ in range(80))


def test_introduction_links_appear_only_after_acceptance(application):
    asker, mine = participant(application, "Intro Asker")
    recipient, theirs = participant(application, "Intro Recipient")
    bystander, _ = participant(application, "Intro Bystander")
    created = asker.post("/api/connection-requests", json={"requester_name": "Intro Asker", "recipient_profile_id": theirs["id"],
                                                          "message": "Coffee about Python?"})
    assert created.status_code == 201
    assert bystander.get("/api/graph").json()["edges"] == []  # an unanswered request reveals nothing
    assert asker.get("/api/graph").json()["edges"] == []
    assert recipient.post(f"/api/connection-requests/{created.json()['token']}/accept").status_code == 200
    link = bystander.get("/api/graph").json()["edges"][0]
    assert {link["a"], link["b"]} == {mine["id"], theirs["id"]} and link["kind"] == "message" and link["confirmed"]


def test_withdrawal_erases_links_and_introductions(application):
    leaver, mine = participant(application, "Leaving Attendee")
    peer, theirs = participant(application, "Staying Peer")
    leaver.post("/api/interactions", json={"target_id": theirs["id"], "kind": "meet"})
    peer.post("/api/interactions", json={"target_id": mine["id"], "kind": "meet"})
    peer.post("/api/connection-requests", json={"requester_name": "Staying Peer", "recipient_profile_id": mine["id"], "message": "Hello there"})
    store = application.state.store
    assert store.memory["interactions"] and store.memory["connections"]
    assert leaver.delete("/api/me").status_code == 204
    assert not store.memory["interactions"] and not store.memory["connections"] and not store.recent_interactions


def test_live_telemetry_requires_a_card(application, client):
    client.post("/api/session")
    assert client.get("/api/telemetry/live").status_code == 403


def test_worker_role_runs_without_the_contact_key():
    settings = Settings(_env_file=None, network_role="worker", environment="production", elasticsearch_url="https://es.example.com",
                        elasticsearch_api_key="k", public_base_url="https://network.example.com", cors_origins="https://network.example.com")
    assert settings.contact_encryption_key is None and settings.network_role == "worker"


def test_timed_out_resume_parses_keep_their_slot_until_the_thread_finishes(application, monkeypatch):
    import threading
    import time

    import app.main as main
    release = threading.Event()
    real_extract = main.extract_text

    def slow_extract(filename, content):
        release.wait(10)
        return real_extract(filename, content)

    monkeypatch.setattr(main, "extract_text", slow_extract)
    monkeypatch.setattr(main, "PARSE_DEADLINE_SECONDS", 0.2)
    # One long-lived client keeps one event loop, as uvicorn does, so slot-release callbacks can run.
    with TestClient(application, headers={"X-NETwork-Request": "1"}) as client:
        client.post("/api/session")

        def upload():
            return client.post("/api/resumes/preview", data={"consent_to_resume_parsing": "true"},
                               files={"resume": ("cv.txt", b"Python engineer. Skills: Python, SQL", "text/plain")})
        try:
            assert [upload().status_code for _ in range(2)] == [400, 400]  # deadline passed, threads still running
            busy = upload()
            assert busy.status_code == 503 and busy.headers["Retry-After"]
        finally:
            release.set()
        for _ in range(50):  # the threads finish and give their slots back
            if not application.state.parse_slots.locked():
                break
            time.sleep(0.05)
        assert upload().status_code == 200
