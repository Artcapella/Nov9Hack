from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import participant, run


def test_requester_cannot_accept_for_recipient(application):
    requester, _ = participant(application, "Demo Requester")
    recipient, card = participant(application, "Demo Recipient", email="synthetic@example.com")
    stranger, _ = participant(application, "Demo Stranger")
    created = requester.post("/api/connection-requests", json={"requester_name": "Forged name", "recipient_profile_id": card["id"], "message": "Discuss Python", "preferred_channel": "email"})
    assert created.status_code == 201
    token = created.json()["token"]
    path = f"/api/connection-requests/{token}"
    assert created.json()["requester_name"] == "Demo Requester"
    denied = requester.post(path + "/accept")
    assert denied.status_code == 403 and "contacts" not in denied.json()
    assert stranger.post(path + "/accept").status_code == 403
    assert stranger.get(path).status_code == 403
    assert requester.get(path + "/contacts").json() == {"status": "pending"}
    assert recipient.post(path + "/accept").status_code == 200
    assert recipient.get(path + "/contacts").status_code == 403
    shared = requester.get(path + "/contacts").json()
    assert shared == {"status": "accepted", "contacts": {"email": "synthetic@example.com"}}
    # The QR token is stored only as a digest, never as a recoverable credential.
    assert token not in str(application.state.store.memory)
    assert recipient.delete("/api/me").status_code == 204
    assert requester.get(path + "/contacts").status_code == 410


def test_connection_expiry_and_contact_policy(application):
    requester, _ = participant(application, "Demo Requester")
    recipient, card = participant(application, "Demo Recipient")
    created = requester.post("/api/connection-requests", json={"requester_name": "Requester", "recipient_profile_id": card["id"], "message": "Discuss Python"}).json()
    item = next(iter(application.state.store.memory["connections"].values()))
    item["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert recipient.post(f"/api/connection-requests/{created['token']}/accept").status_code == 410


@pytest.mark.parametrize("path,method,body", [
    ("/api/me", "get", None), ("/api/me", "delete", None),
    ("/api/profiles", "post", {"display_name": "Demo Attendee", "consent_to_discover": True}),
    ("/api/search", "post", {"question": "Python"}), ("/api/ask", "post", {"question": "Python"}),
    ("/api/resumes/preview", "post", None), ("/api/profiles/from-resume", "post", None),
    ("/api/demo/seed", "post", None),
    ("/api/connection-requests/demo", "get", None),
    ("/api/connection-requests/demo/contacts", "get", None),
    ("/api/connection-requests/demo/accept", "post", None),
    ("/api/connection-requests/demo/qr.png", "get", None),
])
def test_anonymous_requests_cannot_access_participant_data(client, path, method, body):
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401


def test_cookie_origin_csrf_and_unforgeable_profile_identity(client):
    response = client.post("/api/session")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.post("/api/session", headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert client.post("/api/session", headers={"X-NETwork-Request": ""}).status_code == 403
    # Served from a LAN IP or tunnel rather than PUBLIC_BASE_URL: same-origin to the host that served it.
    assert client.post("/api/session", headers={"Origin": "http://192.168.1.20:8000", "Host": "192.168.1.20:8000"}).status_code == 200
    assert client.post("/api/session", headers={"Origin": "https://abc.ngrok.app", "X-Forwarded-Host": "abc.ngrok.app"}).status_code == 200
    assert client.post("/api/session", headers={"Origin": "https://evil.example", "Host": "192.168.1.20:8000"}).status_code == 403
    denied = client.post("/api/profiles", json={"display_name": "Demo Attendee", "consent_to_discover": False})
    assert denied.status_code == 400
    assert client.post("/api/search", json={"question": "Python"}).status_code == 403


def test_preview_is_owned_short_lived_and_only_edited_card_is_public(application):
    client = TestClient(application, headers={"X-NETwork-Request": "1"})
    client.post("/api/session")
    upload = {"resume": ("demo.txt", b"Demo Attendee\nSkills: Python\nExperience\nHIDDEN_UNAPPROVED professional experience\nProjects\nA hidden project detail")}
    assert client.post("/api/resumes/preview", files=upload).status_code == 400
    preview = client.post("/api/resumes/preview", files=upload, data={"consent_to_resume_parsing": "true"})
    assert preview.status_code == 200
    draft = preview.json()["draft_id"]
    stranger = TestClient(application, headers={"X-NETwork-Request": "1"}); stranger.post("/api/session")
    payload = {"display_name": "Demo Attendee", "draft_id": draft, "consent_to_resume_parsing": True,
               "consent_to_discover": True, "approved_experience": "Edited approved experience"}
    assert stranger.post("/api/profiles", json=payload).status_code == 400
    created = client.post("/api/profiles", json=payload)
    assert created.status_code == 201
    store = application.state.store
    public_data = str(store.memory["user_vectors"]) + str(store.memory["knowledge"])
    assert "HIDDEN_UNAPPROVED" not in public_data and "Edited approved experience" in public_data
    assert "HIDDEN_UNAPPROVED" in str(store.memory["resume_vectors"])
    assert not store.memory["drafts"]
    assert client.delete("/api/me").status_code == 204
    assert "HIDDEN_UNAPPROVED" not in str(store.memory["resume_vectors"])
    assert not store.memory["private_contacts"] and not store.memory["resume_structured"]


def test_expired_draft_is_rejected(client):
    client.post("/api/session")
    draft = client.post("/api/resumes/preview", files={"resume": ("demo.txt", b"Demo Attendee\nSkills: Python")}, data={"consent_to_resume_parsing": "true"}).json()["draft_id"]
    client.app.state.store.memory["drafts"][draft]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    result = client.post("/api/profiles", json={"display_name": "Demo Attendee", "consent_to_discover": True, "consent_to_resume_parsing": True, "draft_id": draft})
    assert result.status_code == 400


def test_malicious_contact_urls_never_reach_link_rendering(client):
    client.post("/api/session")
    result = client.post("/api/profiles", json={"display_name": "Demo Attendee", "consent_to_discover": True, "linkedin_url": "javascript:alert(1)"})
    assert result.status_code == 422


def test_public_health_and_event_qr_reveal_no_contacts(client):
    assert client.get("/healthz").json()["mode"] == "memory_demo"
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/event-qr.png").headers["content-type"] == "image/png"
    assert "no-store" in client.get("/api/event-qr.png").headers["cache-control"]


def test_cardless_session_is_forbidden_not_an_elasticsearch_error():
    """Regression: an empty profile id must not reach Elasticsearch GET (ValueError -> HTTP 500)."""
    from unittest.mock import AsyncMock

    from app.config import Settings
    from app.store import NetworkStore

    store = NetworkStore(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None))
    store.client = AsyncMock()
    store.client.get.side_effect = ValueError("Empty value passed for parameter 'id'")
    assert run(store.public_document("")) is None
    store.client.get.assert_not_called()
