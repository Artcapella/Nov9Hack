import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from elasticsearch import AsyncElasticsearch

from app.config import Settings
from app.resume_parser import build_vector_text
from app.schemas import ConnectionRequestCreate, ProfileCreate, PublicProfile, SearchRequest
from app.telemetry import search_counter, search_latency, tracer

logger = logging.getLogger(__name__)


class NetworkStore:
    """Elastic Cloud store with a safe in-memory mode for local UI development."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: AsyncElasticsearch | None = None
        self.memory_profiles: dict[str, dict] = {}
        self.memory_private: dict[str, dict] = {}
        self.memory_connections: dict[str, dict] = {}
        self.memory_knowledge: dict[str, dict] = {}
        self.cipher = Fernet(settings.contact_encryption_key.encode()) if settings.contact_encryption_key else None

    async def start(self) -> None:
        if self.settings.elastic_enabled:
            self.client = AsyncElasticsearch(
                self.settings.elasticsearch_url,
                api_key=self.settings.elasticsearch_api_key,
                verify_certs=self.settings.elasticsearch_verify_certs,
            )
            await self.ensure_indices()
        else:
            logger.warning("No Elasticsearch credentials found; using in-memory demo mode.")

    async def close(self) -> None:
        if self.client:
            await self.client.close()

    async def ensure_indices(self) -> None:
        assert self.client
        semantic = {"type": "semantic_text"}
        if self.settings.elastic_inference_id:
            semantic["inference_id"] = self.settings.elastic_inference_id
        mappings: dict[str, dict] = {
            self.settings.index("user_vectors"): {
                "dynamic": "strict",
                "properties": {
                    "id": {"type": "keyword"}, "event_id": {"type": "keyword"},
                    "display_name": {"type": "keyword"}, "headline": {"type": "text"},
                    "public_summary": {"type": "text"}, "profile_text": {"type": "text"},
                    "profile_semantic": semantic, "can_help_with": {"type": "keyword"},
                    "looking_for": {"type": "keyword"}, "roles": {"type": "keyword"},
                    "formats": {"type": "keyword"}, "availability": {"type": "keyword"},
                    "consent_to_discover": {"type": "boolean"}, "discoverable_until": {"type": "date"},
                    "created_at": {"type": "date"},
                },
            },
            self.settings.index("resume_structured"): {
                "dynamic": "strict",
                "properties": {"profile_id": {"type": "keyword"}, "event_id": {"type": "keyword"}, "structured": {"type": "object", "enabled": False}, "created_at": {"type": "date"}, "resume_sha256": {"type": "keyword"}},
            },
            self.settings.index("private_contacts"): {
                "dynamic": "strict",
                "properties": {"profile_id": {"type": "keyword"}, "encrypted_contacts": {"type": "binary"}, "created_at": {"type": "date"}},
            },
            self.settings.index("knowledge"): {
                "dynamic": "strict",
                "properties": {"id": {"type": "keyword"}, "event_id": {"type": "keyword"}, "title": {"type": "text"}, "source_type": {"type": "keyword"}, "content": {"type": "text"}, "content_semantic": semantic, "profile_id": {"type": "keyword"}},
            },
            self.settings.index("connections"): {
                "dynamic": "strict",
                "properties": {"id": {"type": "keyword"}, "recipient_profile_id": {"type": "keyword"}, "requester_name": {"type": "keyword"}, "message": {"type": "text"}, "preferred_channel": {"type": "keyword"}, "status": {"type": "keyword"}, "token": {"type": "keyword"}, "created_at": {"type": "date"}, "expires_at": {"type": "date"}},
            },
        }
        for index, mapping in mappings.items():
            if not await self.client.indices.exists(index=index):
                await self.client.indices.create(index=index, mappings=mapping)

    async def create_profile(self, profile: ProfileCreate, structured: dict | None = None, resume_sha256: str | None = None) -> PublicProfile:
        now = datetime.now(UTC)
        profile_id = str(uuid.uuid4())
        until = now + timedelta(hours=self.settings.profile_expiry_hours)
        public = {
            "id": profile_id, "event_id": self.settings.demo_event_id,
            "display_name": profile.display_name, "headline": profile.headline,
            "public_summary": profile.public_summary, "can_help_with": profile.can_help_with,
            "looking_for": profile.looking_for, "roles": profile.roles, "formats": profile.formats,
            "availability": profile.availability, "consent_to_discover": profile.consent_to_discover,
            "created_at": now.isoformat(), "discoverable_until": until.isoformat(),
        }
        vector_doc = {**public, "profile_text": build_vector_text(profile.model_dump(), structured), "profile_semantic": build_vector_text(profile.model_dump(), structured)}
        contacts = {"email": str(profile.email) if profile.email else None, "linkedin_url": profile.linkedin_url}
        if self.client:
            await self.client.index(index=self.settings.index("user_vectors"), id=profile_id, document=vector_doc, refresh="wait_for")
            if structured:
                await self.client.index(index=self.settings.index("resume_structured"), id=profile_id, document={"profile_id": profile_id, "event_id": self.settings.demo_event_id, "structured": structured, "resume_sha256": resume_sha256, "created_at": now.isoformat()}, refresh="wait_for")
            await self._save_contacts(profile_id, contacts, now)
            await self.client.index(index=self.settings.index("knowledge"), id=f"profile-{profile_id}", document={"id": f"profile-{profile_id}", "event_id": self.settings.demo_event_id, "title": f"{profile.display_name}: conference profile", "source_type": "opt_in_profile", "content": vector_doc["profile_text"], "content_semantic": vector_doc["profile_text"], "profile_id": profile_id}, refresh="wait_for")
        else:
            self.memory_profiles[profile_id] = vector_doc
            self.memory_private[profile_id] = contacts
            self.memory_knowledge[f"profile-{profile_id}"] = {"title": f"{profile.display_name}: conference profile", "content": vector_doc["profile_text"], "source_type": "opt_in_profile", "profile_id": profile_id}
        return self._public_profile(public)

    async def _save_contacts(self, profile_id: str, contacts: dict, now: datetime) -> None:
        if not any(contacts.values()):
            return
        raw = json.dumps(contacts).encode()
        encrypted = self.cipher.encrypt(raw).decode() if self.cipher else raw.decode()
        assert self.client
        await self.client.index(index=self.settings.index("private_contacts"), id=profile_id, document={"profile_id": profile_id, "encrypted_contacts": encrypted, "created_at": now.isoformat()}, refresh="wait_for")

    async def search_profiles(self, request: SearchRequest) -> list[dict]:
        start = time.perf_counter()
        filters: list[dict] = [
            {"term": {"event_id": self.settings.demo_event_id}},
            {"term": {"consent_to_discover": True}},
            {"range": {"discoverable_until": {"gte": "now"}}},
        ]
        if request.formats:
            filters.append({"terms": {"formats": request.formats}})
        if request.roles:
            filters.append({"terms": {"roles": request.roles}})
        if request.available_now:
            filters.append({"term": {"availability": "now"}})
        if not request.sponsor_intent:
            filters.append({"bool": {"must_not": [{"terms": {"roles": ["sponsor", "recruiter"]}}]}})
        if self.client:
            lexical = {"bool": {"must": [{"multi_match": {"query": request.question, "fields": ["profile_text^3", "headline^2", "can_help_with^2", "looking_for"]}}], "filter": filters}}
            semantic = {"bool": {"must": [{"match": {"profile_semantic": request.question}}], "filter": filters}}
            body = {"size": request.size, "retriever": {"rrf": {"rank_window_size": 50, "rank_constant": 20, "retrievers": [{"standard": {"query": lexical}}, {"standard": {"query": semantic}}]}}}
            try:
                response = await self.client.search(index=self.settings.index("user_vectors"), body=body)
                hits = [{**hit["_source"], "score": hit.get("_score"), "match_reasons": self._reasons(hit["_source"], request.question)} for hit in response["hits"]["hits"]]
            except Exception as exc:
                logger.warning("Hybrid retriever failed; using lexical fallback: %s", exc)
                response = await self.client.search(index=self.settings.index("user_vectors"), query=lexical, size=request.size)
                hits = [{**hit["_source"], "score": hit.get("_score"), "match_reasons": self._reasons(hit["_source"], request.question)} for hit in response["hits"]["hits"]]
        else:
            terms = set(request.question.lower().split())
            candidates = [p for p in self.memory_profiles.values() if p["consent_to_discover"] and p["discoverable_until"] > datetime.now(UTC).isoformat()]
            scored = [(sum(term in p["profile_text"].lower() for term in terms), p) for p in candidates]
            hits = [{**p, "score": score, "match_reasons": self._reasons(p, request.question)} for score, p in sorted(scored, reverse=True, key=lambda item: item[0])[:request.size]]
        elapsed = (time.perf_counter() - start) * 1000
        search_counter.add(1, {"engine": "elasticsearch" if self.client else "memory", "mode": "hybrid"})
        search_latency.record(elapsed, {"engine": "elasticsearch" if self.client else "memory"})
        return hits

    async def retrieve_knowledge(self, question: str, size: int = 5) -> list[dict]:
        if self.client:
            try:
                response = await self.client.search(index=self.settings.index("knowledge"), size=size, retriever={"rrf": {"retrievers": [{"standard": {"query": {"match": {"content": question}}}}, {"standard": {"query": {"match": {"content_semantic": question}}}}]}})
                return [{**hit["_source"], "score": hit.get("_score")} for hit in response["hits"]["hits"]]
            except Exception as exc:
                logger.warning("Knowledge semantic retrieval failed: %s", exc)
        return list(self.memory_knowledge.values())[:size]

    async def create_connection(self, request: ConnectionRequestCreate) -> dict:
        now = datetime.now(UTC)
        item = {"id": str(uuid.uuid4()), "recipient_profile_id": request.recipient_profile_id, "requester_name": request.requester_name, "message": request.message, "preferred_channel": request.preferred_channel, "status": "pending", "token": uuid.uuid4().hex, "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=20)).isoformat()}
        if self.client:
            await self.client.index(index=self.settings.index("connections"), id=item["id"], document=item, refresh="wait_for")
        else:
            self.memory_connections[item["id"]] = item
        return item

    async def get_connection_by_token(self, token: str) -> dict | None:
        if self.client:
            response = await self.client.search(index=self.settings.index("connections"), query={"term": {"token": token}}, size=1)
            return response["hits"]["hits"][0]["_source"] if response["hits"]["hits"] else None
        return next((value for value in self.memory_connections.values() if value["token"] == token), None)

    async def accept_connection(self, token: str) -> dict | None:
        item = await self.get_connection_by_token(token)
        if not item or datetime.fromisoformat(item["expires_at"]) < datetime.now(UTC):
            return None
        item["status"] = "accepted"
        if self.client:
            await self.client.index(index=self.settings.index("connections"), id=item["id"], document=item, refresh="wait_for")
        else:
            self.memory_connections[item["id"]] = item
        contacts = await self._contacts(item["recipient_profile_id"])
        return {**item, "contacts": contacts}

    async def _contacts(self, profile_id: str) -> dict:
        if not self.client:
            return self.memory_private.get(profile_id, {})
        response = await self.client.get(index=self.settings.index("private_contacts"), id=profile_id, ignore=[404])
        if not response.get("found"):
            return {}
        value = response["_source"]["encrypted_contacts"]
        raw = self.cipher.decrypt(value.encode()) if self.cipher else value.encode()
        return json.loads(raw)

    async def seed(self, records: list[dict]) -> int:
        count = 0
        for record in records:
            profile = ProfileCreate(**{**record, "consent_to_discover": True})
            await self.create_profile(profile)
            count += 1
        return count

    def _public_profile(self, document: dict) -> PublicProfile:
        return PublicProfile(**{key: document[key] for key in PublicProfile.model_fields})

    @staticmethod
    def _reasons(profile: dict, question: str) -> list[str]:
        terms = [term for term in question.lower().split() if len(term) > 3]
        text = (profile.get("profile_text") or "").lower()
        exact = [term for term in terms if term in text][:3]
        reasons = []
        if exact:
            reasons.append("Exact expertise overlap: " + ", ".join(exact))
        if profile.get("can_help_with"):
            reasons.append("Offers help with: " + ", ".join(profile["can_help_with"][:3]))
        reasons.append("Opted in and available: " + profile.get("availability", "during the event"))
        return reasons
