"""Live synthetic ingestion/search check; revoke the test card on completion."""
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.agent_tools import definitions
from app.config import get_settings
from app.ingestion import source_record
from app.resume_parser import heuristic_parse
from app.schemas import ProfileCreate, SearchRequest
from app.store import NetworkStore
from app.worker import process_pending


async def main():
    logging.getLogger("elastic_transport").setLevel(logging.CRITICAL)
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("Elasticsearch is required")
    store = NetworkStore(settings)
    await store.start()
    owner = card = None
    try:
        _, owner = await store.new_session()
        content = b"DEMO Integration Check\nExperience\nRestricted original experience in Python"
        parsed = heuristic_parse(content.decode())
        source = source_record(content, "upload", "synthetic_integration_test", owner_id=owner["owner_id"])
        draft = await store.save_draft(owner["owner_id"], parsed, source)
        card = await store.create_profile(ProfileCreate(display_name="DEMO Integration Check", consent_to_discover=True,
            consent_to_resume_parsing=True, draft_id=draft["draft_id"], approved_experience="Reviewed Kubernetes and Python platform engineering",
            can_help_with=["Kubernetes", "Python"], public_summary="Labeled synthetic integration test; removed after verification"), owner)
        await process_pending(store)
        job = await store.get("ingestion_jobs", card["job_id"])
        if job["state"] != "indexed":
            raise RuntimeError("Synthetic publication did not complete: " + job["state"])
        hits = await store.search_profiles(SearchRequest(question="Kubernetes platform engineering"))
        if not hits or any(hit["retrieval_mode"] != "hybrid" for hit in hits):
            raise RuntimeError("Hybrid retrieval could not be verified")
        sources = await store.retrieve_knowledge("Kubernetes platform engineering")
        public = await store.public_document(card["id"])
        assert "Restricted original" not in public["profile_text"]
        resume = await store.get("resume_vectors", card["id"])
        assert resume and resume["model_id"] == settings.elastic_inference_id
        tools = []
        for tool in definitions(settings):
            config = tool["configuration"]
            response = await store.client.esql.query(query=config["query"],
                **({"params": [{"question": "Kubernetes"}]} if config["params"] else {}))
            tools.append({"id": tool["id"], "rows": len(response["values"])})
        evidence = {"checked_at": datetime.now(UTC).isoformat(), "schema": settings.elastic_schema_version,
            "inference_id": settings.elastic_inference_id, "approved_ingestion": True,
            "separate_resume_representation": True, "hybrid_hits": len(hits), "eligible_evidence": len(sources),
            "esql_tools": tools}
    finally:
        if card and owner:
            await store.revoke_profile(card["id"], owner["owner_id"])
            assert not await store.public_document(card["id"])
        if owner:
            await store.delete("sessions", owner["owner_id"])
        await store.close()
    evidence["synthetic_card_revoked_after_check"] = True
    path = Path("docs/evidence/elastic-smoke.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Allowlisted local diagnostics; never print an Elasticsearch error body.
        if isinstance(exc, (RuntimeError, AssertionError)):
            raise SystemExit(str(exc) or "Live smoke assertion failed") from None
        raise SystemExit("Live smoke failed; inspect schema, inference and API privileges") from None
