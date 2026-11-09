"""Restricted records, approved projections, and durable publication jobs."""
import asyncio
import copy
import hashlib
import json
import logging
import secrets
import time
import uuid
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from elasticsearch import AsyncElasticsearch, ConflictError, NotFoundError
from fastapi import HTTPException
from opentelemetry.propagate import inject

from app.config import Settings
from app.elastic_schema import mappings
from app.insights import explain, memory_scores, rrf, topic_summary
from app.people import VectorSpace
from app.policy import SYNTHETIC, branch_queries, describe_filters, eligible, hybrid_body, search_filters, visibility_filters, visible
from app.schemas import ConnectionRequestCreate, ProfileCreate, PublicProfile, SearchRequest
from app.telemetry import fallback_counter, search_counter, search_latency, tracer

logger = logging.getLogger(__name__)

# Collections that are queried by search (not only by id): writes must be visible to the next search.
SEARCHED = {"user_vectors", "knowledge", "ingestion_jobs", "approvals", "connections"}


def now() -> datetime:
    return datetime.now(UTC)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class NetworkStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: AsyncElasticsearch | None = None
        self.memory: dict[str, dict[str, dict]] = {name: {} for name in mappings(settings)}
        self.versions: dict[tuple[str, str], int] = {}
        self.cipher = Fernet(settings.contact_encryption_key.encode()) if settings.contact_encryption_key else None
        self.memory_profiles = self.memory["user_vectors"]
        self.memory_knowledge = self.memory["knowledge"]
        self.space = VectorSpace(self)
        from app.graph import GraphModel
        self.graph = GraphModel(self)

    async def start(self) -> None:
        if self.settings.elastic_enabled:
            if not self.cipher:
                raise RuntimeError("Elasticsearch storage requires CONTACT_ENCRYPTION_KEY")
            self.client = AsyncElasticsearch(self.settings.elasticsearch_url, api_key=self.settings.elasticsearch_api_key,
                                             verify_certs=self.settings.elasticsearch_verify_certs, request_timeout=60)
            if self.settings.elastic_bootstrap_on_start:
                await self.ensure_indices()
        else:
            logger.warning("Using in-memory demo mode; data disappears on restart")

    async def close(self) -> None:
        if self.client:
            await self.client.close()

    async def ensure_indices(self) -> None:
        assert self.client
        await self.client.inference.get(inference_id=self.settings.elastic_inference_id)
        for suffix, mapping in mappings(self.settings).items():
            alias = self.settings.index(suffix)
            if not await self.client.indices.exists(index=alias):
                await self.client.indices.create(index=f"{alias}-000001", mappings=mapping,
                                                 aliases={alias: {"is_write_index": True}})
            else:
                if not await self.client.indices.exists_alias(name=alias):
                    raise RuntimeError(f"{alias} is a concrete legacy index; choose a new ELASTIC_SCHEMA_VERSION")
                existing = await self.client.indices.get_mapping(index=alias)
                missing = {}
                for definition in existing.values():
                    fields = definition["mappings"].get("properties", {})
                    for field, expected in mapping["properties"].items():
                        actual = fields.get(field)
                        if actual is None:
                            missing[field] = expected
                        elif any(actual.get(key) != value for key, value in expected.items()):
                            raise RuntimeError(f"Incompatible schema for {alias}.{field}; choose a new ELASTIC_SCHEMA_VERSION and reindex approved cards")
                if missing:
                    # Purely additive: new optional fields only. Existing documents simply lack them.
                    await self.client.indices.put_mapping(index=alias, properties=missing)

    async def get(self, collection: str, key: str) -> dict | None:
        item = await self.get_versioned(collection, key)
        return item[0] if item else None

    async def get_versioned(self, collection: str, key: str) -> tuple[dict, dict] | None:
        # A session without a card has no profile id; that is a miss (403 upstream), not a 500.
        if not key:
            return None
        if self.client:
            try:
                response = await self.client.get(index=self.settings.index(collection), id=key)
            except NotFoundError:
                return None
            return response["_source"], {"if_seq_no": response["_seq_no"], "if_primary_term": response["_primary_term"]}
        value = self.memory[collection].get(key)
        return (copy.deepcopy(value), {"version": self.versions.get((collection, key), 0)}) if value is not None else None

    async def put(self, collection: str, key: str, value: dict, version: dict | None = None, create: bool = False) -> bool:
        if self.client:
            try:
                semantic = collection in {"user_vectors", "resume_vectors", "knowledge"} and any(field.endswith("_semantic") for field in value)
                span = tracer().start_as_current_span("network.jina.index", record_exception=False, set_status_on_exception=False) if semantic else nullcontext()
                with span:
                    # Only searched collections wait for a refresh (~5 s on Serverless); the rest are read back by id, which is real-time.
                    await self.client.index(index=self.settings.index(collection), id=key, document=value,
                                            refresh="wait_for" if collection in SEARCHED else False,
                                            **(version or {}), **({"op_type": "create"} if create else {}))
            except ConflictError:
                return False
        else:
            current = self.versions.get((collection, key), 0)
            if (create and key in self.memory[collection]) or (version and current != version["version"]):
                return False
            self.memory[collection][key] = copy.deepcopy(value)
            self.versions[collection, key] = current + 1
        return True

    async def delete(self, collection: str, key: str) -> None:
        if self.client:
            try:
                await self.client.delete(index=self.settings.index(collection), id=key, refresh="wait_for" if collection in SEARCHED else False)
            except NotFoundError:
                pass
        else:
            self.memory[collection].pop(key, None)
            self.versions[collection, key] = self.versions.get((collection, key), 0) + 1

    async def new_session(self) -> tuple[str, dict]:
        token = secrets.token_urlsafe(32)
        session = {"event_id": self.settings.demo_event_id, "expires_at": (now() + timedelta(hours=self.settings.session_hours)).isoformat(), "profile_id": None}
        await self.put("sessions", token_hash(token), session, create=True)
        return token, {**session, "owner_id": token_hash(token)}

    async def session(self, token: str | None) -> dict | None:
        if not token or len(token) > 128:
            return None
        result = await self.get("sessions", token_hash(token))
        if not result or result["event_id"] != self.settings.demo_event_id or datetime.fromisoformat(result["expires_at"]) <= now():
            return None
        return {**result, "owner_id": token_hash(token)}

    async def save_draft(self, owner_id: str, parsed, source: dict) -> dict:
        draft_id = secrets.token_urlsafe(24)
        expires_at = (now() + timedelta(minutes=self.settings.draft_minutes)).isoformat()
        await self.put("sources", source["source_id"], source)
        await self.put("drafts", draft_id, {"owner_id": owner_id, "source_id": source["source_id"],
                                           "structured": parsed.structured, "expires_at": expires_at})
        return {"draft_id": draft_id, "expires_at": expires_at, "suggestions": parsed.structured,
                "contact_hints": parsed.contact_hints}

    async def create_profile(self, profile: ProfileCreate, owner: dict | None = None, seed_id: str | None = None,
                             dataset: str | None = None) -> dict:
        if not profile.consent_to_discover:
            raise HTTPException(400, "Discovery consent is required")
        owner_id = owner["owner_id"] if owner else "seed"
        profile_id = token_hash(f"{self.settings.demo_event_id}:{seed_id or owner_id}")
        reviewed = profile.model_dump(exclude={"email", "linkedin_url", "draft_id", "consent_to_resume_parsing"})
        existing = await self.get("approvals", profile_id)
        if existing:
            if existing["owner_id"] != owner_id or existing["state"] == "revoked" or any(existing["public"].get(key) != value for key, value in reviewed.items()):
                raise HTTPException(409, "This session already has a different conference card")
            job_id = f"profile-{profile_id}-{existing['approval_version']}"
            job = await self.get("ingestion_jobs", job_id)
            if job:
                return {**self.public_profile(existing["public"]), "state": job["state"], "job_id": job_id}
        draft = None
        if profile.draft_id:
            draft = await self.get("drafts", profile.draft_id)
            already_saved = existing and existing.get("source_id") and await self.get("resume_structured", profile_id)
            if not already_saved and (not profile.consent_to_resume_parsing or not draft or draft["owner_id"] != owner_id or datetime.fromisoformat(draft["expires_at"]) <= now()):
                raise HTTPException(400, "Resume preview expired or belongs to another session; preview it again")
            if already_saved:
                draft = None
        created = now()
        public = {**reviewed,
                  "id": profile_id, "event_id": self.settings.demo_event_id,
                  "created_at": created.isoformat(), "discoverable_until": (created + timedelta(hours=self.settings.profile_expiry_hours)).isoformat(),
                  "state": "queued", "approval_version": 1}
        if dataset:
            public["dataset"] = dataset
        approval = {"profile_id": profile_id, "event_id": self.settings.demo_event_id, "owner_id": owner_id,
                    "approval_version": 1, "state": "queued", "public": public, "source_id": draft["source_id"] if draft else None}
        if existing:
            approval, public = existing, existing["public"]
        elif not await self.put("approvals", profile_id, approval, create=True):
            raise HTTPException(409, "This session already has a conference card")
        # Bind ownership before further writes so a partial failure is recoverable/withdrawable.
        if owner:
            session = {key: owner[key] for key in ("event_id", "expires_at", "profile_id")}
            session["profile_id"] = profile_id
            await self.put("sessions", owner_id, session)
        await self.put("user_vectors", profile_id, public, create=True)
        contacts = {"email": str(profile.email) if profile.email else None, "linkedin_url": profile.linkedin_url}
        if any(contacts.values()):
            if self.client and not self.cipher:
                raise RuntimeError("Encrypted contacts are required for Elasticsearch")
            raw = json.dumps(contacts).encode()
            value = self.cipher.encrypt(raw).decode() if self.cipher else raw.decode()
            await self.put("private_contacts", profile_id, {"profile_id": profile_id, "encrypted_contacts": value, "created_at": created.isoformat()})
        if draft:
            await self.put("resume_structured", profile_id, {"resume_id": profile_id, "profile_id": profile_id,
                           "source_id": draft["source_id"], "structured": draft["structured"], "approval_version": 1, "created_at": created.isoformat()})
            await self.delete("drafts", profile.draft_id)
        carrier = {}
        inject(carrier)
        job_id = f"profile-{profile_id}-1"
        await self.put("ingestion_jobs", job_id, {"job_id": job_id, "profile_id": profile_id,
                       "event_id": self.settings.demo_event_id, "source_id": approval["source_id"], "kind": "attendee",
                       "approval_version": 1, "state": "queued", "attempts": 0,
                       "created_at": created.isoformat(), "trace_context": carrier}, create=True)
        return {**self.public_profile(public), "state": "queued", "job_id": job_id}

    async def public_document(self, profile_id: str) -> dict | None:
        document = await self.get("user_vectors", profile_id)
        return document if visible(document, self.settings.demo_event_id) else None

    @staticmethod
    def public_profile(document: dict) -> dict:
        return PublicProfile(**document).model_dump(mode="json")

    async def _retrieve(self, collection: str, question: str, filters: list[dict], text_field: str, semantic_field: str, size: int, source: list[str],
                        mode: str = "hybrid") -> tuple[list[dict], str]:
        assert self.client
        index = self.settings.index(collection)
        if mode == "keyword":
            response = await self.client.search(index=index, query=branch_queries(question, filters, text_field, semantic_field)[0], size=size, source=source)
            return [{**hit["_source"], "score": hit.get("_score")} for hit in response["hits"]["hits"]], "keyword"
        if mode == "semantic":
            try:
                response = await self.client.search(index=index, query=branch_queries(question, filters, text_field, semantic_field)[1], size=size, source=source)
            except Exception:
                # Never silently substitute lexical results in a comparison view.
                logger.warning("Semantic retrieval unavailable for comparison")
                fallback_counter.add(1, {"operation": collection})
                return [], "semantic_unavailable"
            return [{**hit["_source"], "score": hit.get("_score")} for hit in response["hits"]["hits"]], "semantic"
        body, lexical = hybrid_body(question, filters, text_field, semantic_field, size, source)
        try:
            response = await self.client.search(index=index, body=body)
            mode = "hybrid"
        except Exception:
            logger.warning("Semantic retrieval unavailable; using filtered lexical retrieval")
            fallback_counter.add(1, {"operation": collection})
            response = await self.client.search(index=index, query=lexical, size=size, source=source)
            mode = "lexical_fallback"
        return [{**hit["_source"], "score": hit.get("_score")} for hit in response["hits"]["hits"]], mode

    def _memory_rank(self, question: str, mode: str) -> list[dict]:
        """Labeled demo ranking: term overlap, transparent concept map, and RRF of both."""
        scored = [(profile, *memory_scores(question, profile)) for profile in self.memory_profiles.values()]
        keyword = sorted((item for item in scored if item[1] > 0), key=lambda item: -item[1])
        concept = sorted((item for item in scored if item[2] > 0), key=lambda item: -item[2])
        if mode == "keyword":
            return [{**profile, "score": k} for profile, k, _ in keyword]
        if mode == "semantic":
            return [{**profile, "score": c} for profile, _, c in concept]
        by_id = {profile.get("id"): profile for profile, _, _ in scored}
        fused = rrf([p.get("id") for p, _, _ in keyword], [p.get("id") for p, _, _ in concept])
        return [{**by_id[key], "score": round(1 / (20 + rank + 1), 4)} for rank, key in enumerate(fused)]

    async def search_profiles(self, request: SearchRequest, exclude_id: str | None = None, viewer: dict | None = None) -> list[dict]:
        started = time.perf_counter()
        source = list(PublicProfile.model_fields) + ["event_id", "consent_to_discover", "state", "profile_text"]
        filters = search_filters(request, self.settings.demo_event_id, exclude_id)
        if self.client:
            hits, mode = await self._retrieve("user_vectors", request.question, filters, "profile_text", "profile_semantic", request.size, source, request.mode)
        else:
            hits, mode = self._memory_rank(request.question, request.mode), "memory_demo"
        gates = describe_filters(search_filters(request, self.settings.demo_event_id, "<your card>" if exclude_id else None))
        found_by = ["keyword", "semantic"] if request.mode == "hybrid" else [request.mode]
        result = []
        for hit in hits:
            if eligible(hit, request, self.settings.demo_event_id, exclude_id):
                result.append({**self.public_profile(hit), "score": hit.get("score"), "retrieval_mode": mode,
                               "match_reasons": self._reasons(hit, request.question),
                               "explanation": explain(hit, request.question, viewer, gates, found_by)})
        search_counter.add(1, {"engine": "elasticsearch" if self.client else "memory", "mode": mode})
        search_latency.record((time.perf_counter() - started) * 1000, {"engine": "elasticsearch" if self.client else "memory"})
        return result[:request.size]

    async def compare_profiles(self, request: SearchRequest, exclude_id: str | None = None, viewer: dict | None = None) -> dict:
        """Run keyword, semantic and hybrid retrieval under identical policy filters."""
        window = request.model_copy(update={"size": min(10, request.size * 2)})
        keyword, semantic, hybrid = await asyncio.gather(*(self.search_profiles(window.model_copy(update={"mode": mode}), exclude_id, viewer)
                                                           for mode in ("keyword", "semantic", "hybrid")))
        membership = {"keyword": {hit["id"] for hit in keyword}, "semantic": {hit["id"] for hit in semantic}}
        for hits in (keyword, semantic, hybrid):
            for hit in hits:
                hit["explanation"]["found_by"] = [name for name, ids in membership.items() if hit["id"] in ids]
        hybrid = hybrid[:request.size]
        return {"engine": "elasticsearch" if self.client else "memory_demo",
                "modes": {"keyword": keyword[:request.size], "semantic": semantic[:request.size], "hybrid": hybrid},
                "retrieval_mode": hybrid[0]["retrieval_mode"] if hybrid else None,
                "missed_by_keyword": [hit["id"] for hit in hybrid if hit["id"] not in membership["keyword"]]}

    async def retrieve_knowledge(self, question: str, size: int = 5, sponsor_intent: bool = False, include_synthetic: bool = True) -> list[dict]:
        size = max(1, min(size, 10))
        if self.client:
            hits, mode = await self._retrieve("knowledge", question, visibility_filters(self.settings.demo_event_id), "content", "content_semantic", size * (1 if include_synthetic else 4),
                         ["id", "title", "content", "source_type", "source_id", "profile_id", "event_id", "state", "consent_to_discover", "discoverable_until"])
        else:
            terms = set(question.casefold().split())
            hits = sorted(self.memory_knowledge.values(), key=lambda p: sum(term in p.get("content", "").casefold() for term in terms), reverse=True)
            mode = "memory_demo"
        evidence = []
        for hit in hits:
            profile = await self.public_document(hit.get("profile_id", ""))
            if (visible(hit, self.settings.demo_event_id) and profile and (sponsor_intent or not {"sponsor", "recruiter"}.intersection(profile.get("roles", [])))
                    and (include_synthetic or profile.get("dataset") != SYNTHETIC)):
                evidence.append({**hit, "retrieval_mode": mode})
        return evidence[:size]

    async def revoke_profile(self, profile_id: str, owner_id: str) -> None:
        approval = await self.get("approvals", profile_id)
        if not approval or approval["owner_id"] != owner_id:
            raise HTTPException(403, "Only the card owner may revoke it")
        for collection, key in (("user_vectors", profile_id), ("knowledge", f"profile-{profile_id}")):
            while True:
                current = await self.get_versioned(collection, key)
                tombstone = {"id": key, "event_id": self.settings.demo_event_id, "state": "revoked", "consent_to_discover": False}
                if await self.put(collection, key, tombstone, current[1] if current else None, create=not current):
                    break
        approval.update(state="revoked", public={})
        await self.put("approvals", profile_id, approval)
        # Restricted vectors also need a tombstone to reject in-flight inference writes.
        while True:
            current = await self.get_versioned("resume_vectors", profile_id)
            if await self.put("resume_vectors", profile_id, {"resume_id": profile_id, "profile_id": profile_id,
                               "state": "revoked", "approval_version": approval["approval_version"]}, current[1] if current else None, create=not current):
                break
        for collection in ("private_contacts", "resume_structured"):
            await self.delete(collection, profile_id)
        await self.finish_job(f"profile-{profile_id}-{approval['approval_version']}", "revoked")

    async def create_connection(self, request: ConnectionRequestCreate, requester: dict) -> dict:
        recipient = await self.public_document(request.recipient_profile_id)
        if not recipient:
            raise HTTPException(404, "Recipient is unavailable")
        if recipient.get("dataset") == SYNTHETIC or requester.get("dataset") == SYNTHETIC:
            raise HTTPException(400, "Introductions to synthetic demo cards are disabled")
        if request.recipient_profile_id == requester["id"]:
            raise HTTPException(400, "Choose another attendee")
        token = secrets.token_urlsafe(32)
        item = {"id": token_hash(token), "event_id": self.settings.demo_event_id,
                "recipient_profile_id": request.recipient_profile_id, "requester_profile_id": requester["id"],
                "requester_name": requester["display_name"], "message": request.message,
                "preferred_channel": request.preferred_channel, "status": "pending",
                "created_at": now().isoformat(), "expires_at": (now() + timedelta(minutes=20)).isoformat()}
        await self.put("connections", item["id"], item, create=True)
        return {**item, "token": token}

    async def get_connection_by_token(self, token: str) -> dict | None:
        item = await self.get("connections", token_hash(token)) if len(token) <= 128 else None
        if not item or item["event_id"] != self.settings.demo_event_id or item["status"] not in {"pending", "accepted"} or datetime.fromisoformat(item["expires_at"]) <= now():
            return None
        if not await self.public_document(item["recipient_profile_id"]) or not await self.public_document(item["requester_profile_id"]):
            return None
        return item

    async def accept_connection(self, token: str, recipient_id: str) -> dict | None:
        item = await self.get_connection_by_token(token)
        if not item:
            return None
        if recipient_id != item["recipient_profile_id"]:
            raise HTTPException(403, "Only the authenticated recipient can accept this introduction")
        item["status"] = "accepted"
        await self.put("connections", item["id"], item)
        return {"status": "accepted"}

    async def connection_for_requester(self, token: str, requester_id: str) -> dict | None:
        item = await self.get_connection_by_token(token)
        if not item:
            return None
        if item["requester_profile_id"] != requester_id:
            raise HTTPException(403, "Only the requester may view shared contact details")
        if item["status"] != "accepted":
            return {"status": item["status"]}
        contacts = await self._contacts(item["recipient_profile_id"])
        channel = item["preferred_channel"]
        if channel in {"email", "linkedin"}:
            key = "email" if channel == "email" else "linkedin_url"
            contacts = {key: contacts.get(key)}
        return {"status": "accepted", "contacts": contacts}

    async def _contacts(self, profile_id: str) -> dict:
        result = await self.get("private_contacts", profile_id)
        if not result:
            return {}
        raw = result["encrypted_contacts"].encode()
        return json.loads(self.cipher.decrypt(raw) if self.cipher else raw)

    async def pending_jobs(self) -> list[dict]:
        if self.client:
            response = await self.client.search(index=self.settings.index("ingestion_jobs"), size=20,
                query={"bool": {"filter": [{"term": {"event_id": self.settings.demo_event_id}}, {"terms": {"state": ["queued", "retry", "processing"]}},
                    {"bool": {"minimum_should_match": 1, "should": [{"range": {"lease_until": {"lte": "now"}}}, {"bool": {"must_not": [{"exists": {"field": "lease_until"}}]}}]}},
                    {"bool": {"minimum_should_match": 1, "should": [{"range": {"next_attempt_at": {"lte": "now"}}}, {"bool": {"must_not": [{"exists": {"field": "next_attempt_at"}}]}}]}}]}}, sort=[{"created_at": "asc"}])
            return [hit["_source"] for hit in response["hits"]["hits"]]
        return [copy.deepcopy(job) for job in self.memory["ingestion_jobs"].values() if job["state"] in {"queued", "retry", "processing"}]

    async def purge_expired(self) -> None:
        """Bounded retention sweeps; expired evidence is already excluded by queries."""
        for collection in ("drafts", "sessions"):
            if self.client:
                await self.client.delete_by_query(index=self.settings.index(collection),
                    query={"range": {"expires_at": {"lte": "now"}}}, conflicts="proceed", refresh=True)
            else:
                for key, record in list(self.memory[collection].items()):
                    if datetime.fromisoformat(record["expires_at"]) <= now():
                        await self.delete(collection, key)
        if self.client:
            response = await self.client.search(index=self.settings.index("user_vectors"), size=20,
                query={"bool": {"filter": [{"term": {"event_id": self.settings.demo_event_id}},
                    {"terms": {"state": ["queued", "processing", "indexed"]}},
                    {"range": {"discoverable_until": {"lte": "now"}}}]}}, source=["id"])
            expired = [hit["_source"]["id"] for hit in response["hits"]["hits"]]
        else:
            expired = [key for key, record in self.memory_profiles.items() if record.get("state") != "revoked"
                       and datetime.fromisoformat(record["discoverable_until"]) <= now()][:20]
        for profile_id in expired:
            approval = await self.get("approvals", profile_id)
            if approval:
                await self.revoke_profile(profile_id, approval["owner_id"])

    async def claim_job(self, job_id: str) -> dict | None:
        current = await self.get_versioned("ingestion_jobs", job_id)
        if not current:
            return None
        job, version = current
        if job.get("event_id") != self.settings.demo_event_id:
            return None
        if job["state"] not in {"queued", "retry", "processing"} or job["attempts"] >= self.settings.worker_max_attempts:
            if job["state"] == "processing" and job["attempts"] >= self.settings.worker_max_attempts and job.get("lease_until", "") <= now().isoformat():
                await self.put("ingestion_jobs", job_id, {**job, "state": "failed", "error_code": "lease_exhausted"}, version)
            return None
        if job.get("lease_until", "") > now().isoformat() or job.get("next_attempt_at", "") > now().isoformat():
            return None
        job.update(state="processing", claim_id=uuid.uuid4().hex, attempts=job["attempts"] + 1,
                   lease_until=(now() + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat())
        return job if await self.put("ingestion_jobs", job_id, job, version) else None

    async def finish_job(self, job_id: str, state: str, claim_id: str | None = None, error_code: str | None = None) -> None:
        while True:
            current = await self.get_versioned("ingestion_jobs", job_id)
            if not current:
                return
            job, version = current
            if claim_id and (job.get("claim_id") != claim_id or job["state"] == "revoked"):
                return
            job.update(state=state, lease_until=now().isoformat())
            if error_code:
                job["error_code"] = error_code
                job["next_attempt_at"] = (now() + timedelta(seconds=2 ** job["attempts"])).isoformat()
            if await self.put("ingestion_jobs", job_id, job, version):
                return

    async def publish_guarded(self, collection: str, key: str, document: dict) -> bool:
        current = await self.get_versioned(collection, key)
        if current and (current[0].get("state") == "revoked" or current[0].get("approval_version", 1) != document.get("approval_version", 1)):
            return False
        if current and collection == "user_vectors" and current[0].get("state") == "indexed" and document.get("state") == "processing":
            # An expired worker lease cannot regress a newer worker's successful publication.
            return True
        return await self.put(collection, key, document, current[1] if current else None, create=not current)

    async def seed(self, records: list[dict], concurrency: int = 6) -> int:
        """Load synthetic cards tagged dataset=SynthDemo. Idempotent per record position; retires older unlabelled seeds."""
        from app.worker import process_pending
        gate = asyncio.Semaphore(concurrency)

        async def retire(seed_key: str):
            legacy_id = token_hash(f"{self.settings.demo_event_id}:{seed_key}")
            legacy = await self.get("approvals", legacy_id)
            if legacy and legacy["owner_id"] == "seed" and legacy["state"] != "revoked":
                await self.revoke_profile(legacy_id, "seed")

        async def one(index: int, record: dict) -> int:
            async with gate:
                for seed_key in (str(index), f"demo-{index}"):  # earlier seed generations, without the dataset tag
                    await retire(seed_key)
                labeled = {**record, "public_summary": "Synthetic demo data. " + record.get("public_summary", "").removeprefix("Synthetic demo data. "),
                           "consent_to_discover": True}
                try:
                    await self.create_profile(ProfileCreate(**labeled), seed_id=f"synth-{index}", dataset=SYNTHETIC)
                    return 1
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    return 0
        count = sum(await asyncio.gather(*(one(index, record) for index, record in enumerate(records))))
        while await process_pending(self, concurrency=concurrency):
            pass
        return count

    # ---- Read models for the demo surfaces: derived from stored records, never simulated ----

    def _model_label(self) -> str:
        return self.settings.elastic_inference_id if self.client else "memory demo · no inference"

    async def pipeline(self, approval: dict) -> dict:
        profile_id, revoked = approval["profile_id"], approval["state"] == "revoked"
        job = await self.get("ingestion_jobs", f"profile-{profile_id}-{approval['approval_version']}")
        source = await self.get("sources", approval["source_id"]) if approval.get("source_id") else None
        records = {name: await self.get(collection, key) for name, collection, key in (
            ("structured", "resume_structured", profile_id), ("restricted", "resume_vectors", profile_id),
            ("public", "user_vectors", profile_id), ("evidence", "knowledge", f"profile-{profile_id}"))}
        has_resume = bool(approval.get("source_id"))

        def state(record, needs_resume=False):
            if needs_resume and not has_resume:
                return "skipped"
            if revoked:
                return "withdrawn"
            return "done" if record and record.get("state", "indexed") == "indexed" else "pending"
        model = self._model_label()
        stages = [
            {"key": "upload", "label": "Resume received",
             "detail": f"{source['source_type'].replace('_', ' ')} · parsed in memory · file discarded, SHA-256 kept" if source else "No resume · card written by hand",
             "state": "withdrawn" if revoked and has_resume else "done" if has_resume else "skipped"},
            {"key": "extraction", "label": "Structured extraction",
             "detail": f"{source['parser_version'] if source else 'parser'} → {self.settings.index('resume_structured')} (restricted)",
             "state": state(records["structured"], True)},
            {"key": "restricted_vector", "label": "Experience embedding",
             "detail": f"{model} → {self.settings.index('resume_vectors')} (restricted)", "state": state(records["restricted"], True)},
            {"key": "approval", "label": "Card approved", "detail": "Only the fields you reviewed become searchable",
             "state": "withdrawn" if revoked else "done"},
            {"key": "public_vector", "label": "Public profile embedding",
             "detail": f"{model} → {self.settings.index('user_vectors')}", "state": state(records["public"])},
            {"key": "evidence", "label": "Concierge evidence", "detail": f"RRF-searchable → {self.settings.index('knowledge')}",
             "state": state(records["evidence"])},
        ]
        job_state = job["state"] if job else approval["state"]
        waiting = next((stage for stage in stages if stage["state"] == "pending"), None)
        if waiting:
            waiting["state"] = "failed" if job_state == "failed" else "active"
        return {"engine": "elasticsearch" if self.client else "memory_demo", "model_id": model,
                "job": {"state": job_state, "attempts": job.get("attempts", 0) if job else 0,
                        "error_code": job.get("error_code") if job else None}, "stages": stages}

    async def trust(self, session: dict) -> dict:
        profile_id = session.get("profile_id")
        approval = await self.get("approvals", profile_id) if profile_id else None
        present: dict[str, bool] = {}
        card = None
        if approval:
            for collection, key in (("user_vectors", profile_id), ("knowledge", f"profile-{profile_id}"), ("resume_structured", profile_id),
                                    ("resume_vectors", profile_id), ("private_contacts", profile_id)):
                document = await self.get(collection, key)
                present[collection] = bool(document) and document.get("state") != "revoked"
            public = approval.get("public") or {}
            card = {"state": approval["state"], "approval_version": approval["approval_version"],
                    "consent_to_discover": approval["state"] != "revoked" and public.get("consent_to_discover") is True,
                    "discoverable_until": public.get("discoverable_until"), "has_resume": bool(approval.get("source_id"))}
        index = self.settings.index
        stores = [
            {"label": "Public card", "index": index("user_vectors"), "access": "public", "present": present.get("user_vectors", False),
             "detail": "Searchable only by opted-in attendees at this event, only while discoverable."},
            {"label": "Concierge evidence", "index": index("knowledge"), "access": "public", "present": present.get("knowledge", False),
             "detail": "Your approved card text, cited by the concierge. Re-checked against your live card on every answer."},
            {"label": "Resume extraction", "index": index("resume_structured"), "access": "restricted", "present": present.get("resume_structured", False),
             "detail": "Parser suggestions. Never queried by attendee search or the agent."},
            {"label": "Experience vectors", "index": index("resume_vectors"), "access": "restricted", "present": present.get("resume_vectors", False),
             "detail": "Restricted embedding of your resume. Tombstoned on withdrawal to reject late writes."},
            {"label": "Contact details", "index": index("private_contacts"), "access": "encrypted" if self.cipher else "memory",
             "present": present.get("private_contacts", False),
             "detail": ("Fernet-encrypted at rest. " if self.cipher else "Memory demo: not encrypted. ") + "Released to one requester only after you accept."},
            {"label": "Resume file", "index": None, "access": "discarded", "present": False,
             "detail": "Read in memory and discarded. Only a SHA-256 content hash and parser version are kept."},
        ]
        gates = describe_filters(search_filters(SearchRequest(question="policy"), self.settings.demo_event_id, "<your card>"))
        return {"engine": "elasticsearch" if self.client else "memory_demo", "event_id": self.settings.demo_event_id, "card": card, "stores": stores,
                "policy_gates": gates,
                "retention": {"resume_preview_minutes": self.settings.draft_minutes, "session_hours": self.settings.session_hours,
                              "card_hours": self.settings.profile_expiry_hours, "introduction_minutes": 20}}

    async def _event_docs(self, collection: str, source: list[str], size: int = 1000) -> list[dict]:
        if self.client:
            response = await self.client.search(index=self.settings.index(collection), size=size, source=source,
                                                query={"term": {"event_id": self.settings.demo_event_id}})
            return [hit["_source"] for hit in response["hits"]["hits"]]
        return [copy.deepcopy(doc) for doc in self.memory[collection].values() if doc.get("event_id") == self.settings.demo_event_id]

    async def ops_summary(self) -> dict:
        """Organizer view: counts and aggregates only — no names, ids, messages or contacts."""
        current = now()
        jobs = await self._event_docs("ingestion_jobs", ["state", "attempts", "error_code", "created_at", "kind"])
        approvals = await self._event_docs("approvals", ["state"])
        cards = await self._event_docs("user_vectors", list(PublicProfile.model_fields) + ["event_id", "consent_to_discover", "state"])
        connections = await self._event_docs("connections", ["status", "expires_at"])
        live = [card for card in cards if visible(card, self.settings.demo_event_id)]
        awaiting_sweep = sum(1 for card in cards if card.get("state") == "indexed" and card.get("discoverable_until", "9") <= current.isoformat())
        pending = [job for job in jobs if job.get("state") in {"queued", "retry", "processing"}]
        connection_states = Counter("expired" if item["status"] == "pending" and item["expires_at"] <= current.isoformat() else item["status"] for item in connections)
        roles = {"user_vectors": "public", "knowledge": "public", "resume_structured": "restricted", "resume_vectors": "restricted",
                 "private_contacts": "restricted", "sources": "restricted", "drafts": "restricted"}
        indices = []
        for collection in ("user_vectors", "knowledge", "resume_vectors", "resume_structured", "private_contacts", "sources", "drafts",
                           "approvals", "ingestion_jobs", "connections", "sessions"):
            if self.client:
                count = (await self.client.count(index=self.settings.index(collection)))["count"]
            else:
                count = len(self.memory[collection])
            indices.append({"name": self.settings.index(collection), "access": roles.get(collection, "system"), "docs": count})
        total_connections = sum(connection_states.values())
        return {
            "generated_at": current.isoformat(), "engine": "elasticsearch" if self.client else "memory_demo",
            "event_id": self.settings.demo_event_id, "inference_id": self._model_label(),
            "queue": {"by_state": dict(Counter(job.get("state") for job in jobs)), "attempts": sum(job.get("attempts", 0) for job in jobs),
                      "retried": sum(1 for job in jobs if job.get("attempts", 0) > 1),
                      "errors": dict(Counter(job["error_code"] for job in jobs if job.get("error_code"))),
                      "oldest_pending_seconds": round(max(((current - datetime.fromisoformat(job["created_at"])).total_seconds() for job in pending), default=0))},
            "consent": {"by_state": dict(Counter(item.get("state") for item in approvals)), "discoverable": len(live), "awaiting_sweep": awaiting_sweep},
            "freshness": {"latest_card": max((card.get("created_at", "") for card in live), default=None),
                          "latest_job": max((job.get("created_at", "") for job in jobs), default=None)},
            "connections": {"by_status": dict(connection_states), "total": total_connections,
                            "acceptance_rate": round(connection_states.get("accepted", 0) / total_connections, 3) if total_connections else None},
            "indices": indices,
            "impact": topic_summary(live),
        }

    @staticmethod
    def _reasons(profile: dict, question: str) -> list[str]:
        terms = [term for term in question.lower().split() if len(term) > 3]
        exact = [term for term in terms if term in profile.get("profile_text", "").lower()][:3]
        reasons = ["Exact expertise overlap: " + ", ".join(exact)] if exact else []
        if profile.get("can_help_with"):
            reasons.append("Offers help with: " + ", ".join(profile["can_help_with"][:3]))
        reasons.append("Opted in and available: " + profile.get("availability", "during the event"))
        return reasons
