import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import qrcode
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.rag import answer_with_evidence
from app.resume_parser import extract_text, heuristic_parse
from app.schemas import AskRequest, ConnectionRequestCreate, ProfileCreate, SearchRequest
from app.store import NetworkStore
from app.telemetry import configure_telemetry, connection_counter, tracer

settings = get_settings()
store = NetworkStore(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.start()
    yield
    await store.close()


app = FastAPI(title="NETwork Concierge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
configure_telemetry(app, settings)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "elasticsearch": settings.elastic_enabled, "event": settings.demo_event_id}


@app.get("/readyz")
async def readyz():
    if settings.elastic_enabled and not store.client:
        raise HTTPException(503, "Elasticsearch client is not ready")
    if store.client and not await store.client.ping():
        raise HTTPException(503, "Elasticsearch is unreachable")
    return {"status": "ready"}


@app.post("/api/profiles", status_code=201)
async def create_profile(profile: ProfileCreate):
    if not profile.consent_to_discover:
        raise HTTPException(400, "An attendee must opt in before becoming discoverable.")
    return await store.create_profile(profile)


@app.post("/api/profiles/from-resume", status_code=201)
async def create_profile_from_resume(
    resume: UploadFile = File(...),
    display_name: str = Form(...),
    headline: str = Form(""),
    public_summary: str = Form(""),
    can_help_with: str = Form("[]"),
    looking_for: str = Form("[]"),
    roles: str = Form('["peer"]'),
    formats: str = Form('["async"]'),
    availability: str = Form("now"),
    email: str | None = Form(None),
    linkedin_url: str | None = Form(None),
    consent_to_discover: bool = Form(False),
    consent_to_resume_parsing: bool = Form(False),
):
    if not consent_to_discover or not consent_to_resume_parsing:
        raise HTTPException(400, "Both discovery and resume-parsing consent are required.")
    if not resume.filename:
        raise HTTPException(400, "A resume filename is required.")
    content = await resume.read()
    if len(content) > 6_000_000:
        raise HTTPException(413, "Resume must be 6 MB or smaller.")
    try:
        parsed = heuristic_parse(extract_text(resume.filename, content))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Explicit form values always win over automatic hints.
    profile = ProfileCreate(
        display_name=display_name or parsed.contact_hints.get("suggested_name") or "Conference attendee",
        headline=headline,
        public_summary=public_summary,
        can_help_with=json.loads(can_help_with) or parsed.structured["skills"],
        looking_for=json.loads(looking_for), roles=json.loads(roles), formats=json.loads(formats),
        availability=availability, email=email or parsed.contact_hints.get("email"),
        linkedin_url=linkedin_url or parsed.contact_hints.get("linkedin_url"),
        consent_to_discover=True, consent_to_resume_parsing=True,
    )
    return await store.create_profile(profile, structured=parsed.structured, resume_sha256=hashlib.sha256(content).hexdigest())


@app.post("/api/resumes/preview")
async def preview_resume(
    resume: UploadFile = File(...),
    consent_to_resume_parsing: bool = Form(False),
):
    """Return editable suggestions; neither bytes nor raw extracted text are retained."""
    if not consent_to_resume_parsing:
        raise HTTPException(400, "Resume-parsing consent is required before previewing suggestions.")
    if not resume.filename:
        raise HTTPException(400, "A resume filename is required.")
    content = await resume.read()
    if len(content) > 6_000_000:
        raise HTTPException(413, "Resume must be 6 MB or smaller.")
    try:
        parsed = heuristic_parse(extract_text(resume.filename, content))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"suggestions": parsed.structured, "contact_hints": parsed.contact_hints}


@app.post("/api/search")
async def search(request: SearchRequest):
    with tracer().start_as_current_span("network.hybrid_search") as span:
        span.set_attribute("network.question.length", len(request.question))
        span.set_attribute("network.sponsor_intent", request.sponsor_intent)
        return await store.search_profiles(request)


@app.post("/api/ask")
async def ask(request: AskRequest):
    with tracer().start_as_current_span("network.rag_answer"):
        return await answer_with_evidence(request.question, store, settings)


@app.post("/api/connection-requests", status_code=201)
async def create_connection(request: ConnectionRequestCreate):
    item = await store.create_connection(request)
    connection_counter.add(1, {"channel": item["preferred_channel"]})
    item["qr_url"] = f"{settings.public_base_url.rstrip('/')}/connect/{item['token']}"
    return item


@app.get("/api/connection-requests/{token}")
async def get_connection(token: str):
    item = await store.get_connection_by_token(token)
    if not item:
        raise HTTPException(404, "Connection request not found")
    return item


@app.post("/api/connection-requests/{token}/accept")
async def accept_connection(token: str):
    item = await store.accept_connection(token)
    if not item:
        raise HTTPException(410, "This connection request is unavailable or expired")
    return item


@app.get("/api/connection-requests/{token}/qr.png")
async def connection_qr(token: str):
    item = await store.get_connection_by_token(token)
    if not item:
        raise HTTPException(404, "Connection request not found")
    image = qrcode.make(f"{settings.public_base_url.rstrip('/')}/connect/{token}")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


@app.get("/api/event-qr.png")
async def event_qr():
    """Print or project this QR so attendees open the mobile-first signup flow."""
    image = qrcode.make(f"{settings.public_base_url.rstrip('/')}/?event={settings.demo_event_id}")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


@app.post("/api/demo/seed")
async def seed_demo():
    if settings.environment == "production":
        raise HTTPException(404, "Not found")
    data_path = "data/seed_profiles.json"
    with open(data_path, encoding="utf-8") as handle:
        return {"seeded": await store.seed(json.load(handle))}


@app.get("/connect/{token}", include_in_schema=False)
async def connection_page(token: str):
    """The same mobile shell renders a token-specific accept screen."""
    return FileResponse("app/static/index.html")


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
