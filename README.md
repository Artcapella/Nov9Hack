# NETwork Concierge

**A mobile-first, QR-accessed conference concierge.** Attendees opt in once, optionally upload a resume for automated structured extraction, and become discoverable through Elasticsearch hybrid search. A judge can ask a real, non-confidential technical question and NETwork finds the opted-in people and sponsor expertise that can help.

## What is deliberately Elastic-native

| Capability | Implementation |
| --- | --- |
| Elastic Cloud Serverless | The only search/data platform for user-vector documents, structured resume records, RAG knowledge, private contacts, and connection events. |
| `semantic_text` / Elastic Inference Service | Generates and stores semantic representations of approved profile text; set `ELASTIC_INFERENCE_ID` to use a chosen EIS endpoint. |
| Hybrid search + RRF | The matching endpoint fuses lexical technology matches with semantic intent search. |
| RAG | `network_knowledge` contains profile and sponsor-evidence documents. The concierge returns sources with its answer. |
| Elastic Agent Builder + MCP | `mcp/elastic-agent-builder.example.json` targets the native Agent Builder MCP endpoint. `mcp/server.py` is a local, read-only fallback with hybrid search and ES|QL tools. |
| Query DSL, filters, and rules | Consent, expiry, availability, connection-format, and sponsor-intent filters are applied to every search. The README supplies a query-rule extension for sponsor cards. |
| ES|QL / aggregations | Connection events and opt-in profile data support live “conference intelligence” views. |

## Privacy boundaries

- Resume files are parsed **in memory** and discarded. The raw file and raw text are never written to disk, Git, logs, or public search documents.
- `network_resume_structured` stores only parsed professional facets; extracted email/LinkedIn values are excluded.
- `network_user_vectors` contains only attendee-approved, discoverable information.
- Contact values are isolated in `network_private_contacts`. Set `CONTACT_ENCRYPTION_KEY` for deployed use. Search never returns contacts.
- A temporary QR request must be accepted before the server returns a recipient’s selected contact methods.
- Synthetic seed data is explicitly demo data. Only QR-form volunteers are live attendee data.

The temporary-QR possession check is appropriate for an in-person demo but is **not** production authentication. Add verified attendee login or magic-link confirmation before production use.

## Local quick start

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# With your connected Elastic Cloud credentials in .env:
python scripts/bootstrap_elastic.py
uvicorn app.main:app --reload
```

Open `http://localhost:8000`. To run a local UI without Elastic credentials, leave the two Elasticsearch values blank; the app uses in-memory demo mode. That mode is only for UI iteration, not for the sponsor demo.

## Cloud setup: required demo path

1. Create or use the existing **Elastic Cloud Serverless Vector Database** project and put `ELASTICSEARCH_URL` plus `ELASTICSEARCH_API_KEY` in `.env`.
2. Give `PUBLIC_BASE_URL` the deployed HTTPS address **before** generating any attendee or connection QR codes.
3. Run `python scripts/bootstrap_elastic.py`; this creates the four `network_*` indices and inserts the labeled demonstration cohort.
4. Open the mobile app from the event QR. Recruit only people who have explicitly opted in.
5. Use the desktop **Ask NETwork** tab for the judge-led query.

Once deployed, print or project `https://YOUR_PUBLIC_HOST/api/event-qr.png`. It resolves to the mobile signup page, while `https://YOUR_PUBLIC_HOST` is the desktop demo console.

## Resume-to-user-vector pipeline

```text
Opt-in mobile upload
  -> in-memory PDF / DOCX / TXT extraction
  -> structured skills + project / experience highlights
  -> attendee approval and public-card fields
  -> network_resume_structured (restricted data)
  -> network_user_vectors.profile_semantic (semantic user-vector document)
  -> network_knowledge (RAG evidence)
```

The prototype uses a fast local heuristic parser so it works immediately. Replace `heuristic_parse` with an approved structured-output LLM only after preserving the same consent boundary and post-parse review UI.

Choosing a resume file triggers an editable preview that can prefill skills, project highlights, email, and LinkedIn. The attendee must review the form and separately consent before the app creates any searchable record.

## Elasticsearch demo script

1. Ask a judge for a **non-confidential** problem: “What are you trying to build or debug?”
2. Enter it into **Ask NETwork**.
3. Explain that the request runs an RRF hybrid query: lexical precision for terms such as `Kubernetes`, plus semantic retrieval for meaning such as `bottleneck` or `debugging`.
4. Open the result’s reasons and identify the live/opt-in profile evidence.
5. Send an async intro. Display the temporary QR; a recipient scans and accepts it before contact details unlock.
6. Switch to Kibana to show the matching index, the query, and the RAG corpus.
7. Ask the same question through your configured Elastic Agent Builder MCP host.

## Elastic Agent Builder and MCP

In Kibana, create a **NETwork Concierge** Agent Builder agent with read-only tools over the `network_user_vectors` and `network_knowledge` indices. Suggested instruction:

> Search only opt-in conference data. Ground every recommendation in retrieved evidence. Never expose contact values or imply expertise absent from a source. Offer a connection request rather than contact information.

Enable the Agent Builder MCP endpoint, set `KIBANA_URL`, and add the native endpoint shown in `mcp/elastic-agent-builder.example.json` to your MCP host. For a self-contained fallback, run:

```bash
python mcp/server.py
```

It exposes `find_conference_expert` (live hybrid retrieval) and `conference_intelligence` (ES|QL) as read-only tools.

### Sponsor-intent query rule (stretch)

Create an Elastic query rule that pins approved sponsor office-hours cards only when the client passes `connection_intent: sponsor`. Never pin sponsors for ordinary peer or mentor requests. This shows a creative use of query rules while preserving attendee agency.

## Dash0: observability track

Set the two `OTEL_*` values from Dash0. The app emits FastAPI request traces plus `network.hybrid_search`, `network.rag_answer`, search-latency, search-request, and connection-request telemetry. For the demo, create an intentional slow query in a feature branch, inspect it with Agent0, fix it, and retain the trace/PR as evidence.

## Radar: Kubernetes / AI SRE track

Build and publish the image, then deploy only the application to Radar’s cluster; **do not self-host Elasticsearch**.

```bash
docker build -t ghcr.io/YOUR_ORG/network-concierge:demo .
docker push ghcr.io/YOUR_ORG/network-concierge:demo
kubectl create secret generic network-concierge-secrets --from-env-file=.env
helm upgrade --install network-concierge deploy/helm/network-concierge \
  --set image.repository=ghcr.io/YOUR_ORG/network-concierge --set image.tag=demo \
  --set ingress.enabled=true --set ingress.host=YOUR_PUBLIC_HOST
```

Use Radar during deployment and perform one real, reversible investigation—for example, deploy a branch with a deliberately incorrect non-secret environment value that makes `/readyz` fail, investigate it, then fix it through a reviewed PR.

## Gitar / Sonar: review track

Install Gitar on this repository before opening feature PRs. `.gitar/review/instructions.md` contains privacy, consent, telemetry, and API-contract rules. Make the work visible through real PRs:

1. Resume parsing + private/public index boundary.
2. Hybrid search, RAG, and QR connection flow.
3. Kubernetes deployment + telemetry.

Create one genuine failing privacy or API-contract test, let Gitar diagnose it, verify the patch, and keep the PR/CI record for judges. Disclose use of coding agents in the project submission and make sure you can explain every component.

## Verification

```bash
python -m compileall app mcp scripts
pytest -q
helm lint deploy/helm/network-concierge
```

## Deliberate cut line

Do not add chat, automatic recruiting, social scraping, demographic inference, real-time location tracking, or a generalized multi-conference admin portal. The complete demo loop is: **QR opt-in → automated resume enrichment → Elastic hybrid/RAG answer → mutual QR intro → observable Kubernetes deployment.**
