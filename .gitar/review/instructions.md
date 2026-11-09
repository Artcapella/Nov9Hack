# NETwork Concierge review instructions

Review every pull request against these non-negotiable conventions:

1. No real resume bytes, raw resume text, contacts, secrets, session credentials or QR tokens may appear in logs, fixtures, public Elasticsearch documents, search responses, screenshots or Git. Privacy tests may use explicitly synthetic text and reserved example.com contacts.
2. A person must actively opt in before their public card is indexed as discoverable. Resume parsing needs separate consent.
3. Only the owned recipient session may accept an introduction. QR possession cannot authorize acceptance. The contacts endpoint may return values only to the original requester after recipient acceptance, while both event cards and the introduction remain valid.
4. Both RRF branches, lexical fallback, memory mode, RAG and MCP must enforce server-authorized event scope, consent, indexed state and expiry. Public embeddings use edited card fields; hidden parser text and restricted corpus records cannot become discovery evidence.
5. Every new public API route needs a negative/privacy test and OpenTelemetry span attributes must never contain user-provided resume text.
6. Prefer a small, observable failure with an actionable test over a hidden fallback that conceals data leakage or search failure.
7. Publication is retryable and idempotent. Check job lease ownership, approval version and revocation races; a failed evidence/inference write must not expose a partially indexed card.
