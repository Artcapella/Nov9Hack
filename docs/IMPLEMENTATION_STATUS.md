# Implementation and sponsor evidence

Updated November 9, 2026. This change implements the next foundation stage of `NETwork_Implementation_Plan.md`. It does not mark the whole roadmap or any sponsor track complete merely because configuration exists.

Local validation: 64 passing pytest cases, Python compilation, JavaScript syntax check, Helm lint and rendered deployment assertions. The test runner reports one upstream Starlette/httpx deprecation warning. Browser verification covered owned card creation, labeled demo search and retrieval-only evidence. Live Elastic checks and the idempotent corpus download are saved under `docs/evidence/`.

| Work | Implementation / observed evidence | Remaining gate |
| --- | --- | --- |
| Recipient identity | Owned HttpOnly sessions; requester/stranger acceptance denied; only requester reads contacts after recipient accepts | Cross-device verified login/recovery is subsequent work |
| Visibility parity | Shared event/consent/expiry/indexed filters in both RRF branches, fallback and memory; current-card checks for evidence | Deployed multi-user check |
| Reviewed publication | Parser preserves sections; editable approved experience; draft ownership/expiry; hidden parser text excluded from public vectors | Real volunteer upload with consent |
| Durable ingestion | Deterministic IDs, CAS leases, retry limits, revocation tombstones, restricted experience vectors, retention sweep | Run worker continuously in Radar |
| Live Jina and search | [Recorded live check](evidence/elastic-smoke.json): v3 aliases, approved ingestion, separate experience representation, hybrid hits, eligible RAG sources, three executed ES|QL tools; synthetic test card revoked | Real opted-in attendee demonstration |
| Corpus acquisition | Allowlisted pinned-IP HTTP download and HTML extraction; licensed synthetic JSON Resume fixture downloaded and published in restricted corpus; no attendee created | Permissioned real corpus acquisition; do not present a sample as a real volunteer |
| Agent Builder and MCP | Converse integration, runtime scope verification, fixed people/sponsor/summary tools, native MCP config and session-scoped local fallback; mocked contracts pass | Set Kibana URL/runtime key, provision tools, execute Converse/MCP and retain tool trace |
| Elastic Workflow | Event-scoped native queue audit YAML supplied | Validate/run in target editor; record successful execution |
| Radar | Helm API + worker + TLS + separate Secrets + probes/resources/non-root containers; local lint/render checks pass | Assigned cluster/namespace, image publication, healthy HTTPS deployment and actual Radar investigation |
| Dash0 | Separate OTLP signal endpoints, dataset/auth headers, process lifecycle, queue trace context, content-free spans and bounded metrics; exported local test spans inspected | Configure dataset/token, inspect a trace from a deployed pod, save dashboard link |
| Sonar / Gitar | Existing repository review instructions retained; CI now includes Helm checks and image build; regression tests cover actual priority issues | Publish requested PR(s), obtain review, verify findings and record links |
| Browser flow | Local browser checked joining, indexed owned card, explicit memory mode, labeled synthetic results, source excerpts and explicit retrieval-only status; script cache versioned | Second-device deployed journey |
| Docker | Build configured in CI | Local daemon unavailable; image build not run locally |

The verified Elastic endpoint is `.jina-embeddings-v5-text-small`. Existing v2 was a concrete prototype index missing new fields; this stage uses v3 write aliases over `*-000001` backing indices. Existing prototype data remains separate. A local encryption key was generated in ignored `.env`; keep that value private and stable. Actual runtime role/index privileges still need operator configuration before deployment. No production access policy is claimed from an index name alone.

Subsequent work packages, in the plan's order:

1. Finish live Agent Builder/Workflow and Radar/Dash0/review acceptance gates with actual sponsor access.
2. Add conference/sponsor publication policies and a separate sponsor-booth catalog, explicit availability/session schedules, room walking graph, step-free meeting points and proposed meeting windows.
3. Add normalized relevance/reciprocity/feasibility scoring, optional topical diversity and a labeled random exploration slot using same-model explicit vectors.
4. Add governed/consented offline demographic-recoverability and qualified-exposure evaluation with person-grouped held-out splits, shuffled baseline, intervals and aggregate reports. No demographic inference is implemented.
5. Expand accessibility checks and optional LinkedIn CSV/ZIP/API access. The current no-resume path, form labels, keyboard controls and live status regions are present; a full accessibility audit is pending.

See [SPONSORS.md](SPONSORS.md) for where each sponsor tool is used.

Before submission, fill actual links and timestamps below. Never substitute a local test or configuration for a live sponsor proof.

- Elastic project/record/query proof: live local smoke record above; add operator-facing project links privately.
- Agent Converse trace: [agent-builder-converse.json](evidence/agent-builder-converse.json) — agent `network-concierge`, hybrid RRF + Jina retrieval, cited answer (2026-11-09).
- Workflow execution: `network-opt-in-pipeline-audit-1`, execution `ec9727b0-6601-48ee-b91c-bd8cd2d66fc3`, completed 2026-11-09T20:39Z ([evidence](evidence/elastic-workflow-and-tools.json)).
- Radar HTTPS URL and investigation: pending.
- Dash0: `network-api` traces and `network.search.requests` metrics received in us-west-2 (2026-11-09, local run); deployed-pod traces carry k8s resource attributes.
- Sonar/Gitar PR/review/fix commits: pending.
- Public repository and three-minute video/submission receipt: pending.
