# Integration runbook

Bootstrap with a temporary administrative Elastic key, then replace it with distinct runtime keys. Keep all key values in Secrets or the ignored local `.env`. The API and worker need the same contact encryption key but separate Elastic keys. The API never sends private contacts, approvals, sources, restricted resume indices or fairness records to Agent Builder.

| Role | Required data access |
| --- | --- |
| Bootstrap | Inspect inference endpoint; create mappings, versioned indices and aliases |
| API | Read/write sessions, drafts, approvals, user projections, evidence, sources, contacts, connections and jobs; delete restricted attendee records on withdrawal |
| Worker | Claim/update ingestion jobs with concurrency controls; read approvals/structured records; write public projections, restricted experience vectors and evidence; expiry cleanup of drafts/sessions/expired attendee content |
| Agent Builder | `agentBuilder:read` plus tools read/execution; read **only approved public projections for the configured event** |
| Provisioning | `agentBuilder:manageTools` and `agentBuilder:manageAgents`; replace with runtime key after creating definitions |
| Offline evaluation | Separate future key for governed evaluation inputs; no chatbot access |

Grant only the `network_*_v3` aliases/backing indices each role needs, including the backing names required by your project's privilege resolution. Avoid `network_*` and unrestricted Kibana/Elasticsearch access for agent callers. Use document-level security or a dedicated event project/index for agent key event isolation; fixed query filters alone are not a replacement for actual privileges. Validate access denial against contacts and corpus indices before enabling the public chatbot. Runtime bootstrap is disabled by default. Serverless role configuration is an operator action, not something these source files enforce automatically.

For Dash0, build a view of:

| Panel | Instrument |
| --- | --- |
| Search throughput | Rate of `network.search.requests`, by engine/mode |
| Search p50/p95 | `network.search.latency` histogram, milliseconds |
| Retrieval fallbacks | Rate of `network.retrieval.fallbacks`, by operation |
| Publication and agent latency | `network.operation.latency`, by operation |
| Ingestion/agent errors | `network.ingestion.failures`, `network.agent.errors` |
| Introductions | `network.connection.requests`, `network.connection.accepted` |

Metric naming may be normalized by the backend; select the exported instruments in the actual dataset. Filter services `network-api` and `network-worker`. Resume parsing, approval, publication, hybrid search and Agent Builder spans intentionally contain no content/contacts/tokens. A queue job carries W3C trace context. Keep host access logs disabled and do not enable automatic HTTP header/body capture. Record one trace from the deployed workload and the dataset/time window in the evidence checklist.

For Radar, inspect namespace, ingress class/TLS handling, registry permissions and quotas before applying Helm values. Use a fresh release name when transitioning from chart 0.1.0; its selectors differ. Check rollout for both deployments and verify HTTPS on another device. Run Radar diagnosis against actual workload events and retain the investigation link; do not fault the healthy application just for a demo.

For Gitar, retain an actual PR review of the negative consent/contact tests and concurrency boundaries, then verify any findings. This change does not create or publish a PR automatically.
