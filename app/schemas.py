from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


ConnectionFormat = Literal["async", "one_to_one", "small_group"]
Role = Literal["peer", "mentor", "mentee", "sponsor", "recruiter"]
Facet = Annotated[str, Field(min_length=1, max_length=100)]


class ProfileCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    headline: str = Field(default="", max_length=160)
    public_summary: str = Field(default="", max_length=1600)
    approved_experience: str = Field(default="", max_length=2400)
    draft_id: str | None = None
    can_help_with: list[Facet] = Field(default_factory=list, max_length=30)
    looking_for: list[Facet] = Field(default_factory=list, max_length=30)
    interests: list[Facet] = Field(default_factory=list, max_length=20)
    roles: list[Role] = Field(default_factory=lambda: ["peer"], min_length=1, max_length=5)
    formats: list[ConnectionFormat] = Field(default_factory=lambda: ["async"], min_length=1, max_length=3)
    availability: str = Field(default="Available during the event", max_length=160)
    email: EmailStr | None = None
    linkedin_url: str | None = Field(default=None, max_length=500)
    consent_to_discover: bool
    consent_to_resume_parsing: bool = False

    @field_validator("linkedin_url")
    @classmethod
    def safe_linkedin(cls, value):
        from urllib.parse import urlsplit
        if value:
            url = urlsplit(value)
            if url.scheme != "https" or url.hostname not in {"linkedin.com", "www.linkedin.com"} or not url.path.startswith("/in/") or url.username or url.password:
                raise ValueError("Use an https://linkedin.com/in/ profile URL")
        return value


class PublicProfile(BaseModel):
    id: str
    display_name: str
    headline: str
    public_summary: str
    approved_experience: str
    can_help_with: list[str]
    looking_for: list[str]
    interests: list[str] = Field(default_factory=list)
    roles: list[str]
    formats: list[str]
    availability: str
    created_at: datetime
    discoverable_until: datetime
    # Set only by administrative seeding ("SynthDemo"); attendees cannot set it.
    dataset: str | None = None


class SearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=700)
    formats: list[ConnectionFormat] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    available_now: bool = False
    sponsor_intent: bool = False
    size: int = Field(default=5, ge=1, le=10)
    mode: Literal["hybrid", "keyword", "semantic"] = "hybrid"
    include_synthetic: bool = True


class PeopleSearchRequest(BaseModel):
    """General similarity view. Attribute names match SearchRequest so the shared policy filters apply."""
    mode: Literal["query", "problem", "hire", "interests", "discover"] = "query"
    question: str = Field(default="", max_length=700)
    formats: list[ConnectionFormat] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    available_now: bool = False
    sponsor_intent: bool = False
    size: int = Field(default=12, ge=1, le=20)
    include_synthetic: bool = True


class SearchHit(PublicProfile):
    score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    explanation: dict = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=700)
    sponsor_intent: bool = False
    include_synthetic: bool = True


class ConnectionRequestCreate(BaseModel):
    requester_name: str = Field(min_length=2, max_length=80)
    recipient_profile_id: str
    message: str = Field(min_length=3, max_length=500)
    preferred_channel: Literal["linkedin", "email", "meetup_qr"] = "meetup_qr"


class ConnectionRequestPublic(BaseModel):
    id: str
    recipient_profile_id: str
    requester_name: str
    message: str
    preferred_channel: str
    status: Literal["pending", "accepted", "declined", "expired"]
    expires_at: datetime
    qr_url: str
