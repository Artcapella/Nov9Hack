# NETwork Concierge review instructions

Review every pull request against these non-negotiable conventions:

1. No real resume bytes, raw resume text, contacts, secrets, session credentials or QR tokens may appear in logs, fixtures, public Elasticsearch documents, search responses, screenshots or Git. Privacy tests may use explicitly synthetic text and reserved example.com contacts.
2. A person must actively opt in before their public card is indexed as discoverable. Resume parsing needs separate consent.
3. Only the owned recipient session may accept an introduction. QR possession cannot authorize acceptance. The contacts endpoint may return values only to the original requester after recipient acceptance, while both event cards and the introduction remain valid.
4. Both RRF branches, lexical fallback, memory mode, RAG and MCP must enforce server-authorized event scope, consent, indexed state and expiry. Public embeddings use edited card fields; hidden parser text and restricted corpus records cannot become discovery evidence.
5. Every new public API route needs a negative/privacy test and OpenTelemetry span attributes must never contain user-provided resume text.
6. Prefer a small, observable failure with an actionable test over a hidden fallback that conceals data leakage or search failure.
7. Publication is retryable and idempotent. Check job lease ownership, approval version and revocation races; a failed evidence/inference write must not expose a partially indexed card.
8. Deployed processes use least-privilege Elasticsearch keys from `scripts/provision_keys.py`: the API key reaches only this schema's NETwork indices, the worker key has no inference, Kibana or contact-key access, and the agent key reads only the public card index. Flag any change that ships the admin key into a pod, widens an index pattern, or adds `manage*` cluster privileges.
9. Every new public route that triggers inference, Agent Builder, parsing or a durable write needs a rate-limit rule in `app/ratelimit.py` and a test that it returns 429 with `Retry-After`. Rejections are counted in `network.security.rejections` with rule or route names only.
10. Interaction links are claims until mutual: a session, message or meeting becomes visible to third parties only when both people logged the same kind, and introductions record "messaged" only on acceptance. Explicit withdrawal must erase the person's interactions and introductions (`NetworkStore.erase_links`). Reject changes that show one-sided claims, pending requests or profile views to anyone outside the pair.
11. Retention or erasure code must never touch other people's records or run a broad delete-by-query without the event filter and a per-person term; the expiry sweep hides data, only an explicit withdrawal deletes links.
12. CI actions stay pinned to commit SHAs; the gitleaks history scan, pip-audit and the Sonar quality gate must pass. Helm pods keep `automountServiceAccountToken: false`, the NetworkPolicies, non-root read-only containers, and immutable image tags.
