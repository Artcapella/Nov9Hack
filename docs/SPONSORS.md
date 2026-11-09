# Sponsor tools: where each one is used

NETwork uses all four sponsor tools in the running product, not only in configuration.

| Sponsor | What it does in NETwork | Code / config | Proof |
| --- | --- | --- | --- |
| **Elastic** | Serverless Elasticsearch stores opted-in cards, the restricted resume corpus and RAG knowledge. Jina `.jina-embeddings-v5-text-small` embeds them through `semantic_text`. Search is RRF hybrid retrieval (BM25 + semantic). The **3D live network** reads the Jina vectors Elastic stored with each card (`_inference_fields`) and projects them with PCA, so the map shows the index itself. The SynthDemo cohort is a `dataset` keyword filter in every query. An **Agent Builder** agent (`network-concierge`) with three event-scoped ES\|QL tools writes the cited answer. An **Elastic Workflow** audits the opt-in pipeline every 15 minutes. Least-privilege API keys per component ([scripts/provision_keys.py](../scripts/provision_keys.py)). The Workflow also flags abuse: interaction, introduction and session bursts per 15 minutes. | [app/store.py](../app/store.py), [app/rag.py](../app/rag.py), [app/agent_tools.py](../app/agent_tools.py), [scripts/provision_agent.py](../scripts/provision_agent.py), [deploy/elastic/ingestion-queue-audit.yaml](../deploy/elastic/ingestion-queue-audit.yaml), [mcp/](../mcp/) | [agent-builder-converse.json](evidence/agent-builder-converse.json), [elastic-workflow-and-tools.json](evidence/elastic-workflow-and-tools.json), [elastic-smoke.json](evidence/elastic-smoke.json) |
| **Radar** | The API and ingestion worker run on Kubernetes through the Helm chart. The Radar agent (Skyhook chart) connects the cluster to Radar Cloud for workload health, events and troubleshooting. The chart is hardened for Radar to show: NetworkPolicies, no service-account tokens, immutable image tags, and the Radar agent itself has no Secret read, exec or port-forward rights. | [deploy/helm/network-concierge/](../deploy/helm/network-concierge/), [scripts/deploy_k8s.sh](../scripts/deploy_k8s.sh) | Radar Cloud cluster view: healthy `network` namespace workloads |
| **Dash0** | OpenTelemetry traces and metrics from the API (`network-api`) and the worker (`network-worker`) export over OTLP/HTTP to Dash0. The spans cover hybrid search, the People view, the network graph (`network.graph`), the RAG answer, the Agent Builder converse call and worker publication; metrics cover search count and latency, fallbacks and agent errors. Kubernetes pod and namespace resource attributes link each trace to the workload Radar shows. Spans and metric labels never include resume text or contacts. Application warnings also export as OTLP logs, `network.security.rejections` counts CSRF, rate-limit and 401/403 refusals, and [deploy/dash0/check-rules.yaml](../deploy/dash0/check-rules.yaml) alerts on abuse and reliability. | [app/telemetry.py](../app/telemetry.py), [scripts/demo_traffic.py](../scripts/demo_traffic.py) | Dash0 → Services → `network-api`; metric `network.search.requests` |
| **Sonar / Gitar** | Every pull request is reviewed against the repository privacy conventions in [.gitar/review/instructions.md](../.gitar/review/instructions.md). CI runs tests, Helm lint and an image build. The Gitar rules now cover scoped keys, rate limits, mutual-consent links and erasure. CI pins actions to SHAs, scans history with gitleaks, audits dependencies, and runs a SonarQube Cloud quality gate with coverage when `SONAR_TOKEN` is set. | [.gitar/](../.gitar/), [.github/workflows/ci.yml](../.github/workflows/ci.yml) | PR review threads and the fix commits that address them |

## Reproduce

```bash
python -m scripts.provision_agent --apply        # Agent Builder tools and agent (admin key)
python -m scripts.provision_keys --apply         # least-privilege API/worker/agent keys -> .env.api, .env.worker
python -m scripts.smoke_elastic                   # live Jina + hybrid + ES|QL check; synthetic card revoked
RADAR_TOKEN=... RADAR_CLUSTER=... CLIENT_IP_HEADER=cf-connecting-ip scripts/deploy_k8s.sh   # app + worker + Radar agent
kubectl apply -n network -f deploy/dash0/check-rules.yaml   # Dash0 check rules (Dash0 operator)
kubectl -n network port-forward svc/network-network-concierge 8080:8000
python -m scripts.demo_traffic http://localhost:8080 3    # labeled synthetic traffic for Dash0; probe revoked
```

Set `KIBANA_URL` to the project's `.kb.` host (the Elasticsearch URL with `.es.` replaced by `.kb.`). Set `DASH0_OTLP_HTTP_BASE` to the regional ingress, for example `https://ingress.us-west-2.aws.dash0.com`, and leave off any `/v1/...` path.
