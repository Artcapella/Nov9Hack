from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


ConnectionFormat = Literal["async", "one_to_one", "small_group"]
Role = Literal["peer", "mentor", "mentee", "sponsor", "recruiter"]


class ProfileCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    headline: str = Field(default="", max_length=160)
    public_summary: str = Field(default="", max_length=1600)
    can_help_with: list[str] = Field(default_factory=list)
    looking_for: list[str] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=lambda: ["peer"])
    formats: list[ConnectionFormat] = Field(default_factory=lambda: ["async"])
    availability: str = Field(default="Available during the event", max_length=160)
    email: EmailStr | None = None
    linkedin_url: str | None = Field(default=None, max_length=500)
    consent_to_discover: bool
    consent_to_resume_parsing: bool = False


class PublicProfile(BaseModel):
    id: str
    display_name: str
    headline: str
    public_summary: str
    can_help_with: list[str]
    looking_for: list[str]
    roles: list[str]
    formats: list[str]
    availability: str
    created_at: datetime
    discoverable_until: datetime


class SearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=700)
    formats: list[ConnectionFormat] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    available_now: bool = False
    sponsor_intent: bool = False
    size: int = Field(default=5, ge=1, le=10)


class SearchHit(PublicProfile):
    score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=700)


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
