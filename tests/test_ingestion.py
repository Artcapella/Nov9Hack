import socket
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.ingestion import download, import_source, resolve_public
from app.store import NetworkStore
from app.worker import process_pending
from conftest import run


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"])
def test_import_rejects_internal_or_multicast_dns(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, (address, 443))])
    with pytest.raises(ValueError):
        resolve_public("https://resumes.example.com/demo.txt", ["resumes.example.com"])


@pytest.mark.parametrize("url", ["file:///tmp/resume", "https://unapproved.example/demo", "https://user:password@resumes.example.com/demo", "https://resumes.example.com:8443/demo", "https://resumes.example.com/demo?secret=token"])
def test_import_requires_exact_allowlist_and_no_url_credentials(url):
    with pytest.raises(ValueError):
        resolve_public(url, ["resumes.example.com"])


def test_redirect_is_validated_and_download_is_size_bounded(monkeypatch):
    settings = Settings(_env_file=None, resume_source_hosts=["resumes.example.com"], import_min_interval_seconds=0)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 443))])
    response = SimpleNamespace(status=302, getheader=lambda key: "http://127.0.0.1/private" if key == "Location" else None)
    connection = SimpleNamespace(request=lambda *a, **kw: None, getresponse=lambda: response, close=lambda: None)
    monkeypatch.setattr("app.ingestion.PinnedHTTP", lambda *a: connection)
    with pytest.raises(ValueError):
        download("https://resumes.example.com/demo", settings)
    response.status = 200
    response.getheader = lambda key: {"Content-Type": "text/plain", "Content-Length": "6000001"}.get(key)
    with pytest.raises(ValueError, match="size"):
        download("https://resumes.example.com/demo", settings)


def test_permitted_corpus_adapter_is_repeatable_and_cannot_create_attendees():
    async def scenario():
        store = NetworkStore(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None))
        entry = {"url": "https://resumes.example.com/demo.txt", "source_type": "download", "acquisition_basis": "owner_permission", "license": "permission_for_research"}
        def fixture_fetch(*args):
            return b"Synthetic Corpus Resume\nExperience\nBuilt a Python search service", "text/plain", entry["url"]
        first = await import_source(store, entry, fixture_fetch)
        second = await import_source(store, entry, fixture_fetch)
        assert first == second
        await process_pending(store)
        assert len(store.memory["sources"]) == len(store.memory["resume_structured"]) == len(store.memory["resume_vectors"]) == 1
        assert not store.memory["user_vectors"] and not store.memory["knowledge"]
        source = next(iter(store.memory["sources"].values()))
        assert source["source_url"] == entry["url"] and len(source["content_hash"]) == 64
        assert source["license"] == "permission_for_research"
    run(scenario())


def test_json_resume_adapter_keeps_only_professional_sections():
    async def scenario():
        store = NetworkStore(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None))
        entry = {"url": "https://resumes.example.com/example.json", "source_type": "download", "format": "jsonresume", "acquisition_basis": "licensed_synthetic", "license": "MIT"}
        sample = {"basics": {"email": "synthetic@example.com", "address": "PRIVATE_ADDRESS"},
                  "work": [{"position": "Engineer", "highlights": ["Built Python services"]}],
                  "skills": [{"name": "Python", "keywords": ["APIs"]}]}
        await import_source(store, entry, lambda *args: (json.dumps(sample).encode(), "application/json", entry["url"]))
        await process_pending(store)
        assert "Built Python services" in str(store.memory["resume_vectors"])
        assert "synthetic@example.com" not in str(store.memory) and "PRIVATE_ADDRESS" not in str(store.memory)
    run(scenario())
