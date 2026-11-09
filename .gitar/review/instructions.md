# NETwork Concierge review instructions

Review every pull request against these non-negotiable conventions:

1. No resume bytes, raw resume text, email, LinkedIn URL, secrets, or QR tokens may appear in logs, test fixtures, public Elasticsearch documents, API search responses, screenshots, or the repository.
2. A person must actively opt in before their public card is indexed as discoverable. Resume parsing needs separate consent.
3. Contact details may be returned only by the accept-connection endpoint after a valid, unexpired mutual-introduction flow.
4. Elasticsearch queries must filter `consent_to_discover: true` and an unexpired `discoverable_until` value.
5. Every new public API route needs a negative/privacy test and OpenTelemetry span attributes must never contain user-provided resume text.
6. Prefer a small, observable failure with an actionable test over a hidden fallback that conceals data leakage or search failure.
