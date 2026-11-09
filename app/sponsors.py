"""Fictional demo booths, separate from real attendees and live availability."""

BOOTHS = [
    {"id": "elastic", "name": "Elastic", "mark": "E", "booth": "01", "focus": "Search & AI",
     "description": "Explore hybrid search, semantic retrieval, and grounded AI answers.",
     "topics": ["Elasticsearch", "Jina embeddings", "Agent Builder"],
     "prompt": "Help me build hybrid search and RAG with Elasticsearch",
     "contribution": "Powers discovery, profile embeddings, and the conference concierge."},
    {"id": "radar", "name": "Radar", "mark": "R", "booth": "02", "focus": "Kubernetes operations",
     "description": "Talk through workloads, deployment health, and Kubernetes troubleshooting.",
     "topics": ["Kubernetes", "Deployment health", "Troubleshooting"],
     "prompt": "Help me debug Kubernetes workloads and deployment health with Radar",
     "contribution": "Watches the Kubernetes environment that hosts the API and worker."},
    {"id": "dash0", "name": "Dash0", "mark": "D", "booth": "03", "focus": "Observability",
     "description": "Connect traces, metrics, and logs to understand a slow application.",
     "topics": ["OpenTelemetry", "Traces & metrics", "Performance"],
     "prompt": "Help me trace slow application requests with Dash0 and OpenTelemetry",
     "contribution": "Receives API and worker telemetry to make request performance visible."},
    {"id": "gitar", "name": "Gitar", "mark": "G", "booth": "04", "focus": "Code quality",
     "description": "Discuss code review, maintainability, and privacy-aware engineering.",
     "topics": ["Code review", "Privacy", "Developer workflow"],
     "prompt": "Help me improve code reviews and privacy checks with Gitar",
     "contribution": "Reviews pull requests against this project's privacy and quality rules."},
]
