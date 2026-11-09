import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def application():
    return create_app(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None,
                               openai_api_key=None, agent_builder_enabled=False), telemetry=False)


@pytest.fixture
def client(application):
    with TestClient(application, headers={"X-NETwork-Request": "1"}) as client:
        yield client


def participant(application, name="Demo Attendee", **fields):
    client = TestClient(application, headers={"X-NETwork-Request": "1"})
    assert client.post("/api/session").status_code == 200
    response = client.post("/api/profiles", json={"display_name": name, "consent_to_discover": True,
                                               "can_help_with": ["Python"], **fields})
    assert response.status_code == 201, response.text
    return client, response.json()


def run(awaitable):
    return asyncio.run(awaitable)
