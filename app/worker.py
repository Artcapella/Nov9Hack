"""Lease-based worker, also used inline for the explicitly labeled memory demo."""
import asyncio
import logging
import signal
import tempfile
import time
from pathlib import Path

from opentelemetry.propagate import extract

from app.config import get_settings
from app.resume_parser import build_vector_text
from app.store import NetworkStore, now
from app.telemetry import configure_telemetry, ingestion_failures, operation_latency, shutdown_telemetry, tracer

logger = logging.getLogger(__name__)


async def publish_job(store: NetworkStore, job: dict) -> str:
    if job["kind"] == "corpus":
        record = await store.get("resume_structured", job["resume_id"])
        if not record:
            return "revoked"
        await index_experience(store, record)
        return "indexed"
    profile_id = job["profile_id"]
    approval_record = await store.get_versioned("approvals", profile_id)
    if not approval_record:
        return "revoked"
    approval, approval_cas = approval_record
    public = approval["public"]
    if approval["event_id"] != store.settings.demo_event_id or approval["state"] == "revoked" or approval["approval_version"] != job["approval_version"] or not public.get("consent_to_discover") or public.get("discoverable_until", "") <= now().isoformat():
        return "revoked"
    structured = await store.get("resume_structured", profile_id)
    if structured:
        await index_experience(store, structured)
    text = build_vector_text(public)
    vector = {**public, "state": "processing", "profile_text": text, "profile_semantic": text,
              "experience_semantic": public["approved_experience"], "seeking_semantic": "\n".join(public["looking_for"]),
              "model_id": store.settings.elastic_inference_id}
    # Empty semantic_text values need not invoke inference.
    vector = {key: value for key, value in vector.items() if key not in {"experience_semantic", "seeking_semantic"} or value}
    if not await store.publish_guarded("user_vectors", profile_id, vector):
        return "revoked"
    evidence_id = f"profile-{profile_id}"
    evidence = {"id": evidence_id, "event_id": public["event_id"], "state": "indexed", "consent_to_discover": True,
                "approval_version": job["approval_version"], "discoverable_until": public["discoverable_until"],
                "title": f"{public['display_name']}: conference profile", "source_type": "opt_in_profile",
                "content": text, "content_semantic": text, "profile_id": profile_id, "source_id": approval.get("source_id")}
    if not await store.publish_guarded("knowledge", evidence_id, evidence):
        return "revoked"
    # Visibility flips only after every inference/indexing operation succeeded.
    if not await store.publish_guarded("user_vectors", profile_id, {**vector, "state": "indexed"}):
        return "revoked"
    if not await store.put("approvals", profile_id, {**approval, "state": "indexed"}, approval_cas):
        # Revocation raced with restricted vector indexing: remove any late restricted write.
        latest = await store.get("approvals", profile_id)
        if latest and latest["state"] == "revoked":
            return "revoked"
    return "indexed"


async def index_experience(store: NetworkStore, record: dict):
    text = "\n".join(record["structured"].get("experience_highlights", []) + record["structured"].get("project_highlights", []) + record["structured"].get("skills", []))
    if text:
        await store.publish_guarded("resume_vectors", record["resume_id"], {"resume_id": record["resume_id"], "profile_id": record.get("profile_id"),
                        "source_id": record["source_id"], "experience_text": text, "experience_semantic": text,
                        "model_id": store.settings.elastic_inference_id, "state": "indexed", "approval_version": record["approval_version"]})


async def process_pending(store: NetworkStore) -> int:
    completed = 0
    for pending in await store.pending_jobs():
        job = await store.claim_job(pending["job_id"])
        if not job:
            continue
        started = time.perf_counter()
        with tracer().start_as_current_span("network.ingestion.publish", context=extract(job.get("trace_context", {})), record_exception=False, set_status_on_exception=False):
            try:
                state = await publish_job(store, job)
                await store.finish_job(job["job_id"], state, job["claim_id"])
                completed += state == "indexed"
            except Exception:
                # Persist an allowlisted code, never the exception text or parser contents.
                state = "failed" if job["attempts"] >= store.settings.worker_max_attempts else "retry"
                await store.finish_job(job["job_id"], state, job["claim_id"], "publication_unavailable")
                ingestion_failures.add(1, {"kind": job["kind"], "outcome": state})
                logger.warning("Publication unavailable; job retained for retry")
            operation_latency.record((time.perf_counter() - started) * 1000, {"operation": "publication"})
    return completed


async def main():
    settings = get_settings().model_copy(update={"otel_service_name": "network-worker"})
    if not settings.elastic_enabled:
        raise SystemExit("The standalone worker requires Elasticsearch; memory mode runs inline")
    configure_telemetry(None, settings)
    store = NetworkStore(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass  # Windows local worker can still be stopped with Ctrl+C.
    await store.start()
    last_sweep = 0
    try:
        while not stop.is_set():
            Path(tempfile.gettempdir(), "network-worker-heartbeat").touch()
            try:
                if time.monotonic() - last_sweep > 60:
                    await store.purge_expired()
                    last_sweep = time.monotonic()
                await process_pending(store)
            except Exception:
                logger.warning("Queue unavailable; worker will reconnect")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await store.close()
        shutdown_telemetry()


if __name__ == "__main__":
    asyncio.run(main())
