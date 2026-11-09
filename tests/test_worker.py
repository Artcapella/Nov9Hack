import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.ingestion import source_record
from app.resume_parser import heuristic_parse
from app.schemas import ProfileCreate
from app.store import NetworkStore
from app.worker import process_pending
from conftest import run


async def queued_resume():
    store = NetworkStore(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None))
    _, owner = await store.new_session()
    content = b"Demo Attendee\nExperience\nPrivate Python engineering experience"
    parsed = heuristic_parse(content.decode())
    draft = await store.save_draft(owner["owner_id"], parsed, source_record(content, "upload", "authorized", owner_id=owner["owner_id"]))
    card = await store.create_profile(ProfileCreate(display_name="Demo Attendee", consent_to_discover=True,
                    draft_id=draft["draft_id"], consent_to_resume_parsing=True, approved_experience="Reviewed Python expertise"), owner)
    return store, owner, card


def test_durable_publication_is_idempotent_and_not_visible_before_indexing():
    async def scenario():
        store, owner, card = await queued_resume()
        assert not await store.public_document(card["id"])
        assert await process_pending(store) == 1
        assert await process_pending(store) == 0
        assert len(store.memory["user_vectors"]) == len(store.memory["resume_vectors"]) == 1
        assert (await store.get("ingestion_jobs", card["job_id"]))["state"] == "indexed"
        assert (await store.public_document(card["id"]))["model_id"] == store.settings.elastic_inference_id
        assert "Private Python" not in str(store.memory["user_vectors"])
    run(scenario())


def test_only_one_worker_claims_a_lease_and_abandoned_leases_recover():
    async def scenario():
        store, owner, card = await queued_resume()
        claims = await asyncio.gather(store.claim_job(card["job_id"]), store.claim_job(card["job_id"]))
        assert sum(claim is not None for claim in claims) == 1
        first_claim = next(claim for claim in claims if claim)
        store.memory["ingestion_jobs"][card["job_id"]]["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        second_claim = await store.claim_job(card["job_id"])
        assert second_claim["attempts"] == 2 and second_claim["claim_id"] != first_claim["claim_id"]
        await store.finish_job(card["job_id"], "indexed", first_claim["claim_id"])
        assert (await store.get("ingestion_jobs", card["job_id"]))["state"] == "processing"
    run(scenario())


@pytest.mark.parametrize("collection", ["user_vectors", "knowledge", "resume_vectors"])
def test_revocation_wins_a_race_with_inference_publication(collection):
    async def scenario():
        store, owner, card = await queued_resume()
        original = store.put
        triggered = False
        async def racing_put(target, key, value, version=None, create=False):
            nonlocal triggered
            if target == collection and value.get("state") == "indexed" and not triggered:
                triggered = True
                await store.revoke_profile(card["id"], owner["owner_id"])
            return await original(target, key, value, version, create)
        store.put = racing_put
        await process_pending(store)
        assert triggered
        assert not await store.public_document(card["id"])
        assert (await store.get("ingestion_jobs", card["job_id"]))["state"] == "revoked"
        assert not store.memory["resume_structured"]
        assert "Private Python" not in str(store.memory["resume_vectors"])
        assert "Reviewed Python" not in str(store.memory["user_vectors"]) + str(store.memory["knowledge"])
    run(scenario())


def test_model_errors_retry_with_bounded_codes_without_retaining_sensitive_errors(monkeypatch, caplog):
    async def fail(*args):
        raise RuntimeError("PRIVATE_RESUME_CONTENT")
    monkeypatch.setattr("app.worker.publish_job", fail)
    async def scenario():
        store, owner, card = await queued_resume()
        for attempt in range(1, 4):
            await process_pending(store)
            job = await store.get("ingestion_jobs", card["job_id"])
            assert job["attempts"] == attempt
            assert job["state"] == ("failed" if attempt == 3 else "retry")
            assert job["error_code"] == "publication_unavailable"
            store.memory["ingestion_jobs"][card["job_id"]]["next_attempt_at"] = datetime.now(UTC).isoformat()
        assert not await store.public_document(card["id"])
        assert "PRIVATE_RESUME_CONTENT" not in str(store.memory)
    run(scenario())
    assert "PRIVATE_RESUME_CONTENT" not in caplog.text


def test_evidence_index_failure_never_exposes_a_partially_indexed_card():
    async def scenario():
        store, owner, card = await queued_resume()
        original = store.publish_guarded
        async def fail_evidence(collection, key, document):
            if collection == "knowledge":
                raise RuntimeError("inference unavailable")
            return await original(collection, key, document)
        store.publish_guarded = fail_evidence
        await process_pending(store)
        assert not await store.public_document(card["id"])
        assert (await store.get("ingestion_jobs", card["job_id"]))["state"] == "retry"
    run(scenario())


def test_retention_sweep_clears_expired_previews_sessions_contacts_and_vectors():
    async def scenario():
        store, owner, card = await queued_resume()
        await process_pending(store)
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        store.memory["sessions"][owner["owner_id"]]["expires_at"] = past
        store.memory["drafts"]["old-preview"] = {"owner_id": owner["owner_id"], "expires_at": past}
        store.memory["user_vectors"][card["id"]]["discoverable_until"] = past
        await store.purge_expired()
        assert not store.memory["sessions"] and not store.memory["drafts"]
        assert not await store.public_document(card["id"])
        assert "Private Python" not in str(store.memory["resume_vectors"])
    run(scenario())


def test_partial_approval_write_is_owned_and_retryable():
    async def scenario():
        store = NetworkStore(Settings(_env_file=None, elasticsearch_url=None, elasticsearch_api_key=None))
        token, owner = await store.new_session()
        original = store.put
        failed = False
        async def fail_once(collection, *args, **kwargs):
            nonlocal failed
            if collection == "user_vectors" and not failed:
                failed = True
                raise RuntimeError("transient storage failure")
            return await original(collection, *args, **kwargs)
        store.put = fail_once
        profile = ProfileCreate(display_name="Demo Retry", consent_to_discover=True)
        with pytest.raises(RuntimeError):
            await store.create_profile(profile, owner)
        assert (await store.session(token))["profile_id"]
        card = await store.create_profile(profile, owner)
        await process_pending(store)
        repeated = await store.create_profile(profile, owner)
        assert card["id"] == repeated["id"] and repeated["state"] == "indexed"
        assert len(store.memory["ingestion_jobs"]) == 1
    run(scenario())


def test_stale_worker_cannot_regress_successfully_published_card():
    async def scenario():
        store, _, card = await queued_resume()
        await process_pending(store)
        current = await store.public_document(card["id"])
        assert await store.publish_guarded("user_vectors", card["id"], {**current, "state": "processing"})
        assert await store.public_document(card["id"])
    run(scenario())
