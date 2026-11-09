import io
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import interactions, live, meetings
from app.config import Settings, get_settings
from app.ingestion import source_record
from app.rag import answer_with_evidence
from app.resume_parser import extract_text, heuristic_parse
from app.people import people_search
from app.schemas import AskRequest, ConnectionRequestCreate, InteractionCreate, NetworkingBreak, PeopleSearchRequest, ProfileCreate, SearchRequest
from app.store import NetworkStore
from app.telemetry import accepted_counter, configure_telemetry, connection_counter, instrument_app, shutdown_telemetry, tracer

COOKIE = "network_session"


def create_app(settings: Settings | None = None, telemetry: bool = True) -> FastAPI:
    settings = settings or get_settings()
    store = NetworkStore(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if telemetry:
            configure_telemetry(None, settings)
        try:
            await store.start()
            yield
        finally:
            await store.close()
            if telemetry:
                shutdown_telemetry()

    app = FastAPI(title="NETwork Concierge", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    origins.append(settings.public_base_url.rstrip("/"))
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-NETwork-Request", "X-NETwork-Ops-Token"])
    if telemetry:
        instrument_app(app)

    def forwarded(request: Request, name: str) -> str | None:
        value = request.headers.get(f"x-forwarded-{name}")
        return value.split(",")[0].strip() if value else None

    def same_origin(request: Request) -> bool:
        # Accept the configured origins plus whatever host actually served the page (LAN IP, tunnel, 127.0.0.1).
        # The custom X-NETwork-Request header is the real CSRF guard: cross-site pages cannot send it without a
        # CORS preflight, which CORSMiddleware refuses for unlisted origins.
        origin = request.headers.get("origin")
        if not origin or origin in origins:
            return True
        served_host = forwarded(request, "host") or request.headers.get("host")
        return bool(served_host) and urlsplit(origin).netloc == served_host

    @app.middleware("http")
    async def protect_browser_requests(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("X-NETwork-Request") != "1" or not same_origin(request):
                return JSONResponse({"detail": "Same-origin application request required"}, status_code=403)
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/") and request.url.path != "/api/telemetry/live":
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            live.record(request.method, route, response.status_code, (time.perf_counter() - started) * 1000)
        if request.url.path.startswith(("/api/", "/connect/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "; ".join(error["msg"] for error in exc.errors())}, status_code=422)

    async def require_session(request: Request):
        session = await store.session(request.cookies.get(COOKIE))
        if not session:
            raise HTTPException(401, "Start an event session first")
        return session

    async def require_attendee(session: dict = Depends(require_session)):
        profile = await store.public_document(session.get("profile_id") or "")
        if not profile:
            raise HTTPException(403, "Join this event and wait for your approved card to be indexed")
        return profile

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "elasticsearch": settings.elastic_enabled, "mode": "elastic" if settings.elastic_enabled else "memory_demo", "event": settings.demo_event_id}

    @app.get("/readyz")
    async def readyz():
        try:
            if settings.elastic_enabled and (not store.client or not await store.client.ping() or not await store.client.indices.exists(index=settings.index("user_vectors"))):
                raise RuntimeError()
        except Exception:
            raise HTTPException(503, "Elasticsearch or schema is unavailable; run bootstrap") from None
        return {"status": "ready"}

    @app.post("/api/session")
    async def start_session(request: Request, response: Response):
        session = await store.session(request.cookies.get(COOKIE))
        if not session:
            token, session = await store.new_session()
            # Secure follows the scheme this browser actually used, so the session cookie also sticks on plain-HTTP LAN hosts.
            https = (forwarded(request, "proto") or request.url.scheme) == "https"
            response.set_cookie(COOKIE, token, httponly=True, secure=https,
                                samesite="strict", max_age=settings.session_hours * 3600, path="/")
        return {"event_id": session["event_id"], "profile_id": session["profile_id"],
                "mode": "elastic" if settings.elastic_enabled else "memory_demo"}

    @app.get("/api/me")
    async def me(session: dict = Depends(require_session)):
        approval = await store.get("approvals", session.get("profile_id") or "")
        if not approval:
            return {"profile": None}
        job = await store.get("ingestion_jobs", f"profile-{approval['profile_id']}-{approval['approval_version']}")
        return {"profile": store.public_profile(approval["public"]) if approval["public"] else None,
                "state": job["state"] if job else approval["state"], "pipeline": await store.pipeline(approval)}

    @app.get("/api/trust")
    async def trust(session: dict = Depends(require_session)):
        return await store.trust(session)

    @app.delete("/api/me", status_code=204)
    async def revoke(session: dict = Depends(require_session)):
        if not session.get("profile_id"):
            raise HTTPException(404, "No conference card in this session")
        await store.revoke_profile(session["profile_id"], session["owner_id"])

    @app.post("/api/profiles", status_code=201)
    async def create_profile(profile: ProfileCreate, session: dict = Depends(require_session)):
        with tracer().start_as_current_span("network.approval", record_exception=False, set_status_on_exception=False):
            result = await store.create_profile(profile, session)
            if not settings.elastic_enabled:
                from app.worker import process_pending
                await process_pending(store)
                job = await store.get("ingestion_jobs", result["job_id"])
                result["state"] = job["state"]
            return result

    @app.post("/api/profiles/from-resume")
    async def legacy_resume(session: dict = Depends(require_session)):
        raise HTTPException(409, "Preview the resume, review the editable card, then POST /api/profiles with draft_id")

    @app.post("/api/resumes/preview")
    async def preview_resume(resume: UploadFile = File(...), consent_to_resume_parsing: bool = Form(False),
                             source_type: Literal["upload", "linkedin_export"] = Form("upload"),
                             session: dict = Depends(require_session)):
        if not consent_to_resume_parsing:
            raise HTTPException(400, "Resume-parsing consent is required")
        if not resume.filename:
            raise HTTPException(400, "A filename is required")
        content = await resume.read(settings.import_max_bytes + 1)
        if len(content) > settings.import_max_bytes:
            raise HTTPException(413, "Resume must be 6 MB or smaller")
        try:
            with tracer().start_as_current_span("network.resume.parse", record_exception=False, set_status_on_exception=False):
                parsed = heuristic_parse(extract_text(resume.filename, content))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        source = source_record(content, source_type=source_type, basis="attendee_authorized_upload", owner_id=session["owner_id"])
        return await store.save_draft(session["owner_id"], parsed, source)

    @app.get("/api/me/break")
    async def get_break(attendee: dict = Depends(require_attendee)):
        return {"break": await meetings.current_break(store, attendee)}

    @app.post("/api/me/break")
    async def set_break(request: NetworkingBreak, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.break.save", record_exception=False, set_status_on_exception=False):
            return {"break": await meetings.save_break(store, attendee, request)}

    @app.delete("/api/me/break", status_code=204)
    async def clear_break(attendee: dict = Depends(require_attendee)):
        await store.delete("networking_breaks", attendee["id"])

    @app.get("/api/people/{profile_id}/overlap")
    async def meeting_overlap(profile_id: str, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.break.overlap", record_exception=False, set_status_on_exception=False):
            return await meetings.overlap(store, attendee, profile_id)

    @app.post("/api/search")
    async def search(request: SearchRequest, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.hybrid_search", record_exception=False, set_status_on_exception=False):
            return await store.search_profiles(request, exclude_id=attendee["id"], viewer=attendee)

    @app.post("/api/search/compare")
    async def compare_search(request: SearchRequest, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.search_compare", record_exception=False, set_status_on_exception=False):
            return await store.compare_profiles(request, exclude_id=attendee["id"], viewer=attendee)

    @app.post("/api/people/search")
    async def search_people(request: PeopleSearchRequest, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.people_search", record_exception=False, set_status_on_exception=False):
            return await people_search(store, request, attendee)

    @app.get("/api/graph")
    async def network_graph(include_synthetic: bool = True, include_sponsors: bool = False, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.graph", record_exception=False, set_status_on_exception=False):
            try:
                return await store.graph.build(attendee, include_synthetic, include_sponsors)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "The network graph is temporarily unavailable") from None

    @app.post("/api/interactions", status_code=201)
    async def create_interaction(request: InteractionCreate, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.interaction", record_exception=False, set_status_on_exception=False):
            return await interactions.record(store, attendee, request.target_id, request.kind)

    elastic_probe: dict = {"at": 0.0, "ms": None, "docs": None}

    @app.get("/api/telemetry/live")
    async def telemetry_live(since: int = 0, session: dict = Depends(require_session)):
        """Aggregate request telemetry (route templates only) plus Elastic round-trip timing."""
        if store.client and time.time() - elastic_probe["at"] > 5:
            started = time.perf_counter()
            try:
                docs = (await store.client.count(index=settings.index("user_vectors")))["count"]
                elastic_probe.update(at=time.time(), ms=round((time.perf_counter() - started) * 1000, 1), docs=docs)
            except Exception:
                elastic_probe.update(at=time.time(), ms=None)
        cached = next(iter(store.graph.cache.values()), None)
        stats = cached["stats"] if cached else {}
        return {**live.snapshot(since), "engine": "elasticsearch" if store.client else "memory_demo",
                "elastic": {"index": settings.index("user_vectors"), "model_id": stats.get("model_id") or store._model_label(),
                            "dimensions": stats.get("dimensions"), "cards": stats.get("count"), "clusters": len(cached["clusters"]) if cached else None,
                            "explained_variance": stats.get("explained_variance"), "count_ms": elastic_probe["ms"], "index_docs": elastic_probe["docs"],
                            "links_ms": store.graph.last_took_ms},
                "dash0": bool(settings.dash0_otlp_http_base or settings.otel_exporter_otlp_endpoint)}

    @app.get("/api/ops/summary")
    async def ops_summary(request: Request):
        supplied = request.headers.get("X-NETwork-Ops-Token", "")
        if settings.ops_token:
            if not secrets.compare_digest(supplied.encode(), settings.ops_token.encode()):
                raise HTTPException(401, "Organizer token required")
        elif settings.environment == "production" or settings.elastic_enabled:
            raise HTTPException(404, "Set OPS_TOKEN to enable the operations view")
        try:
            return await store.ops_summary()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Operations data is temporarily unavailable") from None

    @app.post("/api/ask")
    async def ask(request: AskRequest, attendee: dict = Depends(require_attendee)):
        with tracer().start_as_current_span("network.rag_answer", record_exception=False, set_status_on_exception=False):
            return await answer_with_evidence(request.question, store, settings, request.sponsor_intent, request.include_synthetic)

    @app.post("/api/connection-requests", status_code=201)
    async def create_connection(request: ConnectionRequestCreate, attendee: dict = Depends(require_attendee)):
        item = await store.create_connection(request, attendee)
        connection_counter.add(1, {"channel": item["preferred_channel"]})
        await interactions.record(store, attendee, request.recipient_profile_id, "message")
        item["qr_url"] = f"{settings.public_base_url.rstrip('/')}/connect/{item['token']}"
        return item

    @app.get("/api/connection-requests/{token}")
    async def get_connection(token: str, attendee: dict = Depends(require_attendee)):
        item = await store.get_connection_by_token(token)
        if not item:
            raise HTTPException(410, "Connection request is unavailable or expired")
        if attendee["id"] not in {item["requester_profile_id"], item["recipient_profile_id"]}:
            raise HTTPException(403, "This introduction belongs to another attendee")
        return item

    @app.post("/api/connection-requests/{token}/accept")
    async def accept_connection(token: str, attendee: dict = Depends(require_attendee)):
        connection = await store.get_connection_by_token(token)
        item = await store.accept_connection(token, attendee["id"])
        if not item:
            raise HTTPException(410, "Connection request is unavailable or expired")
        accepted_counter.add(1)
        await interactions.record(store, attendee, connection["requester_profile_id"], "message")
        return item

    @app.get("/api/connection-requests/{token}/contacts")
    async def connection_contacts(token: str, attendee: dict = Depends(require_attendee)):
        item = await store.connection_for_requester(token, attendee["id"])
        if not item:
            raise HTTPException(410, "Connection request is unavailable or expired")
        return item

    def qr_response(url):
        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="image/png")

    @app.get("/api/connection-requests/{token}/qr.png")
    async def connection_qr(token: str, attendee: dict = Depends(require_attendee)):
        await get_connection(token, attendee)
        return qr_response(f"{settings.public_base_url.rstrip('/')}/connect/{token}")

    @app.get("/api/event-qr.png")
    async def event_qr():
        return qr_response(f"{settings.public_base_url.rstrip('/')}/")

    @app.post("/api/demo/seed")
    async def seed_demo(session: dict = Depends(require_session)):
        if settings.environment == "production" or settings.elastic_enabled:
            raise HTTPException(404, "Use the administrative seed command")
        with open("data/seed_profiles.json", encoding="utf-8") as handle:
            return {"seeded": await store.seed(json.load(handle))}

    @app.get("/connect/{token}", include_in_schema=False)
    async def connection_page(token: str):
        return FileResponse("app/static/index.html")

    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
    return app


app = create_app()
