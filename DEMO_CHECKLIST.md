# NETwork Concierge demo checklist

## Before attendees arrive

- [ ] Set the HTTPS `PUBLIC_BASE_URL` in `.env`.
- [ ] Verify `/healthz`, `/readyz`, and `/api/event-qr.png` on the deployed URL.
- [ ] Run `python -m scripts.bootstrap_elastic` and the worker; seed records are labeled DEMO and cannot accept real introductions.
- [ ] Configure contact encryption, separate runtime keys, scoped Elastic agent tools and Dash0 credentials. Check the [implementation evidence](docs/IMPLEMENTATION_STATUS.md).
- [ ] Open the mobile QR on one phone and the Ask NETwork console on a laptop.
- [ ] Ask 5–10 volunteers to complete the opt-in card; do not enter anyone manually without consent.
- [ ] Ensure the Elastic index / Dev Tools, Dash0 view, Radar environment, and PR history are each in a browser tab.

## Live judge flow

1. **Join.** Upload or fill in a card. The pipeline strip under the header shows each real stage (resume received → restricted extraction → restricted experience embedding → approval → public embedding → concierge evidence) as the worker's job advances.
2. **Ask something fuzzy**, e.g. “people building accessible climate tools” or a judge's own non-confidential problem. Or press **Find my people** to search from your own card.
3. **Toggle Keyword / Semantic / Hybrid.** The summary says how many people hybrid found that BM25 missed; those cards are tinted and badged “missed by keyword.” Every mode runs under identical policy filters.
4. Open **Why this match?** on a card: exact BM25 terms, shared concepts, reciprocity (they offer what you need / you offer what they need), fields searched, and the exact policy gates passed.
5. Open the concierge **Tool trace**: the verified ES|QL / retriever steps and row counts. Model-supplied parameters are never shown.
6. **Field tab.** Request an introduction to a real opted-in volunteer: a dashed link animates from your node. When they accept (scanning in the browser they joined with) and you check contacts, the link turns solid with traffic both ways.
7. **Trust** (top right): consent state, which indices hold what (public / restricted / encrypted / discarded), retention windows, and the exact gates. Withdraw from there to show every store emptying.
8. **Ops tab** (organizer token): queue states, attempts/retries, error codes, consent counts, index doc counts, freshness, topic/cluster aggregates. No names or messages.
9. Close with the Dash0 trace and Radar deployment evidence, then point to Gitar-reviewed PRs.

The memory demo labels its semantic mode as a transparent concept map, not Jina. Only claim Jina semantic retrieval when the compare summary says Elasticsearch.

## Honest fallback

If the judge’s question is too niche or live signups are sparse, say so and use this seeded, clearly marked question:

> “Our Kubernetes application is slow and we cannot locate the bottleneck.”

It should retrieve a clearly labeled synthetic observability/SRE card and, only if sponsor intent is selected, a synthetic sponsor card. Do not describe seed data as real attendees or inferred meeting availability. Show the actual retrieval mode; a lexical or retrieval-only fallback is not proof of live semantic or agent execution.
