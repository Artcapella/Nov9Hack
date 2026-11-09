# NETwork Concierge

A mobile conference concierge: preview a resume, review a professional card, opt into event discovery, search approved expertise, and exchange contacts after the recipient approves an introduction.

This implementation delivers the plan's next foundation stage: secure contact/consent boundaries, durable ingestion, pinned Jina representations, scoped retrieval and Elastic agent integration, plus worker/telemetry/deployment support. Schedule/travel planning, a separate sponsor-booth catalog, diversity ranking, and offline fairness evaluation remain subsequent work. See [implementation status](docs/IMPLEMENTATION_STATUS.md) for evidence and outstanding integration gates.

**Sponsor tools in use** ([details and proof](docs/SPONSORS.md)):
**Elastic** (Serverless, Jina embeddings, RRF hybrid search, an Agent Builder agent with ES|QL tools, a Workflow and MCP),
**Radar** (Helm deployment to Kubernetes, watched by the Radar agent),
**Dash0** (OTLP traces and metrics from the API and worker) and
**Gitar** (privacy-focused PR review).

## Run locally

### Networking additions (November 9)

- **People → Meet someone unexpected** samples real, discoverable attendees across the Elasticsearch index with a fresh random seed. Consent, expiry, event, role, format and sponsor-intent filters apply. It ignores search text and shows no invented similarity percentage. Synthetic cards are always excluded from this mode; other views now default to hiding them for new browsers.
- **People → My networking break** stores one attendee-entered time window and public meeting area per owned card in `network_networking_breaks_v3`. Open another real attendee's card and choose **Find a shared break** to calculate an overlap of at least five minutes. Both attendees must share availability. Matching area names are exact (case-insensitive), not geographic proximity. This is a suggestion, not a booking or an inferred panel schedule. Breaks are removed on withdrawal and swept after expiry by the worker.

Before running the updated API/worker on another environment, run `.venv-next\Scripts\python -m scripts.enable_networking --apply` with its configured Elastic credentials. This creates only the new index/alias and verifies the random query; it never seeds attendees. Runtime keys need read/write/delete access to that index and the worker needs its retention-cleanup privileges. The existing memory mode remains available for isolated tests.

Live verification on November 9 succeeded: break mapping and random query verified, zero eligible real candidates, zero stored breaks, zero attendee records created. Two-person live overlap verification requires actual opted-in attendees to share their own availability. Local tests cover overlap, isolation, expiry, withdrawal, validation and random-query filters. The UI was checked against the live-connected local API; Kubernetes image rollout is separate.

Python 3.12 or 3.13 is supported. In this workspace, `.venv-next` is the working environment; the original `.venv` points to a missing interpreter.

```powershell
python -m venv .venv-next
.venv-next\Scripts\python -m pip install -r requirements.txt
# For a new checkout only; preserve an existing .env.
Copy-Item .env.example .env
.venv-next\Scripts\python -m uvicorn app.main:app --reload --no-access-log
```

Open [the local app](http://localhost:8000). With blank Elastic settings it displays **memory demo** and uses the same publication, authorization and visibility checks with lexical search. It processes jobs inline; records disappear on restart. The same browser session owns the card and must approve its introductions. Join before searching; another person can join in a different browser/device. A QR contains an introduction capability, never the recipient's login.

For managed Elasticsearch, set the URL/API key and a stable Fernet `CONTACT_ENCRYPTION_KEY` in the ignored `.env`, then run:

```powershell
.venv-next\Scripts\python -m scripts.verify_elastic
.venv-next\Scripts\python -m scripts.bootstrap_elastic
.venv-next\Scripts\python -m app.worker
# In another terminal:
.venv-next\Scripts\python -m uvicorn app.main:app --no-access-log
```

**Joining** starts with the resume: drop a PDF/DOCX/TXT/MD, tick parsing consent, and **Autofill my card** fills name, headline (latest role · organisation), skills (from the skills section plus a known-terms list), projects, experience, email and LinkedIn (a bare `LinkedIn (handle)` becomes a profile URL). **Skip** goes straight to a blank card. Either way the attendee reviews every field; required fields (display name, at least one connection style, discovery consent) are marked `*` and checked before submission.

**SynthDemo cohort.** `python -m scripts.generate_synthetic` writes 160 deterministic fictional cards across six disciplines and their blends to `data/synthetic_profiles.json`. Bootstrap seeds them with the hand-written `data/seed_profiles.json` (171 in total), tagged `dataset: SynthDemo` and summarised as "Synthetic demo data." Seeding runs six at a time and retires the older unlabelled `DEMO ·` seeds. Attendees cannot set `dataset`. The **SynthDemo · shown/hidden** button in the header adds `NOT dataset == SynthDemo` to every search, People view, concierge answer and network map. Introductions to synthetic cards are disabled.

**Live network (2D and 3D tabs, `GET /api/graph`)** reads every discoverable card from `network_user_vectors_v3`, including the Jina vectors Elastic stored for `profile_semantic` (`_inference_fields`, chunks averaged). It does not re-embed them. 3D positions are a PCA projection of those 1024-d vectors; **2D** is the first two components of the same projection (the 3D view seen from the front). Axis signs stay stable between refreshes. **Links exist only where people interacted** (`POST /api/interactions`, stored in `network_interactions_v3`): viewed profile (1) < same session (2) < messaged (3, also logged by requesting or accepting an introduction) < met in person (5), plus a little for repeats. Profile views are visible only to the two people involved; others see the strongest other kind. SynthDemo cards have no real attendees, so their links are simulated deterministically from embedding neighbours and flagged `simulated`. **Clusters** come from k-means (cosine, deterministic k-means++, k ≈ √(n/3)) on the full vectors. Each is named after the `can_help_with`/`looking_for` tags most over-represented among its members compared with the whole room (share × idf). The browser polls every 3 s and flashes nodes and lines for new interactions (`activity`) and joins. **My network** shows only your links, on rings by strength. The 2D HUD and the **Stack** tab read `GET /api/telemetry/live`: per-route counts and p50/p95 latency (route templates only, no IDs or tokens), req/s, and an Elastic round-trip probe. Stack animates each real request along the path it takes. Clicking a person anywhere docks their card, where you can log an interaction. The endpoint requires an attendee card and applies the visibility policy. Sponsors appear only when the Sponsors box is ticked.

**People view** (`POST /api/people/search`) ranks attendees by Jina vector similarity in five modes: search, help with a problem (`offers_semantic`), hiring (recruiters and sponsors), outside work (`interests_semantic`), and find new people (furthest from your discipline cluster). Its filters are the same policy gates every other search uses. The percentage is the cosine recovered from the semantic `_score`. Card and tag colours come from projecting text onto six discipline anchors embedded by the same endpoint; the memory demo labels its concept/term stand-in. The interests, offers and semantic fields are additive: **rerun `scripts.bootstrap_elastic` before deploying** so the existing `v3` indices gain them, or the worker's strict-mapping writes will fail. Cards published earlier lack the new fields until they are re-approved, which happens anyway when they expire.

Bootstrap inspects `.jina-embeddings-v5-text-small`, creates strict versioned backing indices with write aliases, verifies existing schema compatibility, and loads the **synthetic** seed cohort idempotently. It requires index-management permissions; normal startup does not. Defaults now use `network_*_v3` because the existing `network_user_vectors_v2` is an incompatible concrete prototype index. Existing prototype data is not migrated or deleted. Change the schema version and explicitly reapprove/reindex cards for future schema/model migrations; never silently swap models.

## Ownership and privacy

- The server creates an unpredictable HttpOnly, SameSite=Strict session cookie; only its digest is stored. HTTPS adds Secure. Session event scope comes from server configuration. This is device-session ownership, not verification of a person's legal identity. Cross-device login/recovery is subsequent work.
- Search, RAG and introduction actions require an active event card. Requester name is derived from the owned card, preventing request-name impersonation. Every unsafe browser API call requires `X-NETwork-Request: 1` and validates Origin when present.
- The requester and unrelated attendees cannot accept the recipient's request. The recipient approves from their own session. Only the original requester can then fetch shared contacts; expiry or either attendee's revocation closes access.
- Files are size-bounded, parsed in memory and discarded. Preview stores restricted professional suggestions for 20 minutes; contact hints are returned for editing and are not stored in drafts. The public embeddings use **only edited card fields**. Clearing a suggestion removes it from discovery. A source hash and acquisition record preserve provenance.
- A separate restricted resume representation keeps parsed professional experience apart from public experience/seeking/profile semantic fields. All use the same pinned Jina endpoint. No vector arithmetic or fairness mitigation is claimed.
- Queue claims use optimistic concurrency, leases, retry limits and deterministic job IDs. The worker checks approval version/consent; revocation tombstones defeat concurrent publication. The public card becomes indexed only after evidence and inference writes succeed.
- Withdrawal clears public evidence, resume content, private contacts and queued publication. Minimal revocation tombstones and source hashes remain for idempotency/provenance. The worker sweeps expired drafts/sessions and withdraws expired cards every minute. Queries enforce expiry immediately, including before cleanup.
- Interaction links are claims until mutual. A session, message or meeting one person logs is visible only to the pair (marked unconfirmed) until the other logs the same kind; profile views stay private to the pair. Introductions add a "messaged" link in both directions only when the recipient accepts, so an unanswered request reveals nothing. An explicit withdrawal also deletes the person's interactions and introductions; the expiry sweep only hides them.
- Abuse budgets: every `/api/` request spends a per-session token bucket, and costly routes (Agent Builder, inference-backed search, resume parsing, card creation, introductions) have tighter ones; session creation is budgeted per client IP. Refusals return 429 with `Retry-After` and count in `network.security.rejections`. Set `CLIENT_IP_HEADER=cf-connecting-ip` behind a Cloudflare tunnel. Resume parsing runs off the event loop, two at a time, with a 20 s deadline.
- Least privilege: deployed pods never get the admin Elastic key. `python -m scripts.provision_keys --apply` creates an API key (this schema's NETwork indices plus inference), a worker key (same indices, no inference/Kibana) and an Agent Builder key (public card index only), verifies none can manage security, and writes `.env.api` / `.env.worker` for `scripts/deploy_k8s.sh`. The worker runs as `NETWORK_ROLE=worker` without the contact key, ops token or Agent Builder key.
- Elasticsearch contacts always require encryption. Production also requires Elasticsearch and an HTTPS public URL. Public API projections omit ownership IDs, hidden parser suggestions, contact fields and embedding internals.

The upload flow is `POST /api/resumes/preview` → editable review → `POST /api/profiles` with `draft_id`. The legacy direct-file publication endpoint returns a review-required error. Empty/scanned PDFs provide a manual-entry path. Authorized LinkedIn text/document exports can be labeled during preview; direct LinkedIn profile scraping, CSV/ZIP archive import and access by URL alone are not implemented.

## Downloading and scraping a permitted corpus

Administrative imports require an acquisition manifest, an exact hostname allowlist, a license and an acquisition basis. The adapter pins validated public DNS addresses, revalidates every redirect, rejects credentials/query strings, bounds size/time, checks content type, and rate-limits each source host. No external import can create an opted-in attendee. HTML extraction excludes scripts and styles. JSON Resume imports omit basics/contact/address/reference fields.

The checked-in [example manifest](data/permitted_sources.example.json) downloads a licensed **synthetic** JSON Resume fixture. It verifies actual HTTP acquisition; it is not evidence of recruiting or processing a real attendee. Add only permissioned sources for real corpus development. Keep private manifests outside Git.

```powershell
$env:RESUME_SOURCE_HOSTS='["raw.githubusercontent.com"]'
.venv-next\Scripts\python -m scripts.import_resumes --manifest data/permitted_sources.example.json
```

Preserve attribution for the MIT-licensed example; see [third-party notice](docs/JSON_RESUME_NOTICE.md). Other manifests can use `source_type: web` for permitted HTML or `download` for PDF/DOCX/TXT, and `format: jsonresume` for JSON Resume. Imports go only to sources, restricted structured records/vectors and ingestion jobs.

## Hybrid retrieval and Elastic agent

Both RRF branches, lexical fallback and memory mode enforce event, consent, indexed state, expiry, roles, formats, availability and explicit sponsor intent. Search excludes the caller. Evidence also rechecks the current public card. Legacy evidence missing visibility metadata fails closed. Fallbacks are labeled in the UI and counted; a failed Elastic lexical query never returns unrelated memory records.

`/api/ask` uses the [Elastic Converse API](https://www.elastic.co/docs/api/doc/kibana/operation/operation-post-agent-builder-converse), normalizes `response.message`, and returns source IDs and excerpts. It falls back explicitly to retrieval-only when disabled or unavailable. OpenAI generation has been removed from the application path.

Render and review the fixed event-scoped [ES|QL tool definitions](https://www.elastic.co/docs/explore-analyze/ai-features/agent-builder/tools/esql-tools):

```powershell
.venv-next\Scripts\python -m scripts.provision_agent
# Set KIBANA_URL and a provisioning key, then create the tools and agent:
.venv-next\Scripts\python -m scripts.provision_agent --apply
```

Tools are `network.find_people`, `network.find_sponsors` and `network.conference_skill_summary`. Runtime disables built-in Elastic capabilities/skills, restricts tools, verifies each query matches this event/schema, and excludes sponsor search unless requested. Provisioning currently creates definitions; it stops on existing IDs instead of overwriting them. Use a separate runtime key and set `AGENT_BUILDER_ENABLED=true` after validation. No session shares a conversation ID. Treat source text as untrusted evidence; model instructions supplement enforced access boundaries.

The [native MCP configuration](mcp/elastic-agent-builder.example.json) connects a coding host to the Elastic MCP endpoint with a scoped API key. The local stdio fallback runs `python mcp/server.py` as in the example configuration. It calls the participant API using `NETWORK_API_URL` and a privately supplied `NETWORK_SESSION_TOKEN`, so it inherits event and consent policies. Never place the session token in committed MCP configuration. Radar MCP stays outside attendee tooling. Schedule tools will be added with the later schedule package.

Import [the queue audit Workflow](deploy/elastic/ingestion-queue-audit.yaml) in the target Kibana editor, adapt the alias/event if configured differently, validate and run it. The worker already claims approvals from Elastic; the audit demonstrates a native action. A successful live Workflow execution remains an explicit acceptance gate.

## Dash0 and Radar

Set `DASH0_OTLP_HTTP_BASE`, `DASH0_AUTH_TOKEN` and `DASH0_DATASET` from Dash0. Exporters append `/v1/traces` and `/v1/metrics` separately, configure once per process, and flush/shut down at termination. API and worker have distinct service names; trace context travels in queued jobs. No HTTP client auto instrumentation is enabled. Automatic FastAPI spans exclude token paths; run Uvicorn with `--no-access-log` so token URLs stay out of access logs.

Metrics include search requests/latency, lexical fallbacks, ingestion failures, agent errors, accepted introductions, publication/agent latency and security rejections (CSRF, rate limits, 401/403 by route template). Application warnings are exported as OTLP logs (`/v1/logs`) from the `app` logger only, correlated with traces. [deploy/dash0/check-rules.yaml](deploy/dash0/check-rules.yaml) defines Dash0 check rules for rate-limit spikes, cross-site writes, token probing, inference fallbacks, Agent Builder errors, failed publication and slow search. See the [dashboard recipe](docs/OPERATIONS.md). Local trace tests verify exclusion of profile contents, chat text, contacts, cookies and QR tokens. Actual export to a deployed Dash0 dataset still requires verification.

Build and publish the image through your normal registry workflow, inspect the assigned Radar namespace, and supply separate existing API/worker Secrets:

```bash
helm lint deploy/helm/network-concierge
helm template network-concierge deploy/helm/network-concierge --namespace TEAM_NAMESPACE
helm upgrade --install network-concierge deploy/helm/network-concierge \
  --namespace TEAM_NAMESPACE \
  --set image.repository=ghcr.io/TEAM/network-concierge --set image.tag=COMMIT_SHA \
  --set existingSecret=network-concierge-secrets \
  --set worker.existingSecret=network-worker-secrets \
  --set ingress.enabled=true --set ingress.host=PUBLIC_HOST \
  --set ingress.tls[0].secretName=TLS_SECRET --set ingress.tls[0].hosts[0]=PUBLIC_HOST \
  --atomic --wait --timeout 180s
```

The chart renders TLS, non-root read-only API/worker deployments without service-account tokens, NetworkPolicies (API: ingress on its port only; worker: no ingress; both: egress to DNS and HTTPS only), immutable image tags (defaulting to the chart appVersion), writable bounded temporary volumes, readiness/liveness checks, resource limits, image-pull secrets and selectors that route traffic only to the API. Both processes use managed Elasticsearch. The worker requires Elastic and does not run as a separate process in memory mode (`--set worker.enabled=false`). Existing 0.1.0 releases need a fresh release name or a planned deployment replacement because selector labels changed; preserve the healthy release until the replacement is verified.

## Checks and evidence

```powershell
.venv-next\Scripts\python -m compileall -q app mcp scripts
.venv-next\Scripts\python -m pytest -q
node --check app/static/app.js
helm lint deploy/helm/network-concierge
# Uses live Elastic; creates then revokes a labeled synthetic card:
.venv-next\Scripts\python -m scripts.smoke_elastic
```

CI (actions pinned to commit SHAs) runs a gitleaks full-history secret scan, pip-audit, Python checks with coverage, Helm lint/render assertions, an image build. Gitar reviews every pull request against `.gitar/review/instructions.md`; configuration and passing local tests do not establish a completed sponsor review. Retain actual PR/review, Workflow, Radar and Dash0 records in the [evidence checklist](docs/IMPLEMENTATION_STATUS.md). No public deployment or sponsor account setup is claimed by this source change.
