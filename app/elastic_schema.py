"""Versioned mappings shared by bootstrap, API and ingestion worker."""
from app.config import Settings


def mappings(settings: Settings) -> dict[str, dict]:
    keyword = {"type": "keyword"}
    date = {"type": "date"}
    text = {"type": "text"}
    obj = {"type": "object", "enabled": False}
    integer = {"type": "integer"}
    semantic = {"type": "semantic_text", "inference_id": settings.elastic_inference_id}
    visibility = {"event_id": keyword, "consent_to_discover": {"type": "boolean"},
                  "discoverable_until": date, "state": keyword, "approval_version": integer}
    public = {
        **visibility, "id": keyword, "display_name": keyword, "headline": text,
        "public_summary": text, "approved_experience": text, "can_help_with": keyword,
        "looking_for": keyword, "interests": keyword, "roles": keyword, "formats": keyword, "availability": keyword,
        "created_at": date, "profile_text": text, "profile_semantic": semantic,
        "experience_semantic": semantic, "seeking_semantic": semantic, "offers_semantic": semantic,
        "interests_semantic": semantic, "model_id": keyword, "dataset": keyword,
    }
    definitions = {
        "user_vectors": public,
        "approvals": {"profile_id": keyword, "event_id": keyword, "owner_id": keyword,
                      "approval_version": integer, "state": keyword, "public": obj, "source_id": keyword},
        "sessions": {"event_id": keyword, "expires_at": date, "profile_id": keyword},
        "drafts": {"owner_id": keyword, "source_id": keyword, "expires_at": date, "structured": obj},
        "sources": {"source_id": keyword, "source_type": keyword, "source_url": keyword,
                    "acquisition_basis": text, "license": keyword, "content_hash": keyword,
                    "parser_version": keyword, "acquired_at": date},
        "resume_structured": {"resume_id": keyword, "profile_id": keyword, "source_id": keyword,
                              "structured": obj, "approval_version": integer, "created_at": date},
        "resume_vectors": {"resume_id": keyword, "profile_id": keyword, "source_id": keyword,
                           "experience_text": text, "experience_semantic": semantic, "model_id": keyword,
                           "state": keyword, "approval_version": integer},
        "private_contacts": {"profile_id": keyword, "encrypted_contacts": {"type": "binary"}, "created_at": date},
        "knowledge": {**visibility, "id": keyword, "title": text, "source_type": keyword,
                      "content": text, "content_semantic": semantic, "profile_id": keyword, "source_id": keyword},
        "ingestion_jobs": {"job_id": keyword, "profile_id": keyword, "source_id": keyword,
                           "resume_id": keyword, "event_id": keyword, "kind": keyword,
                           "approval_version": integer, "state": keyword, "attempts": integer,
                           "lease_until": date, "claim_id": keyword, "next_attempt_at": date,
                           "trace_context": obj, "error_code": keyword, "created_at": date},
        "connections": {"id": keyword, "event_id": keyword, "recipient_profile_id": keyword,
                        "requester_profile_id": keyword, "requester_name": keyword, "message": text,
                        "preferred_channel": keyword, "status": keyword, "created_at": date, "expires_at": date},
    }
    return {name: {"dynamic": "strict", "properties": properties} for name, properties in definitions.items()}
