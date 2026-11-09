# NETwork Concierge demo checklist

## Before attendees arrive

- [ ] Set the HTTPS `PUBLIC_BASE_URL` in `.env`.
- [ ] Verify `/healthz`, `/readyz`, and `/api/event-qr.png` on the deployed URL.
- [ ] Run `python scripts/bootstrap_elastic.py`; keep seed records visibly labeled as demo data.
- [ ] Open the mobile QR on one phone and the Ask NETwork console on a laptop.
- [ ] Ask 5–10 volunteers to complete the opt-in card; do not enter anyone manually without consent.
- [ ] Ensure the Elastic index / Dev Tools, Dash0 view, Radar environment, and PR history are each in a browser tab.

## Live judge flow

1. Ask for a non-confidential project or technical problem.
2. Enter it in **Ask NETwork** and say: “This is RRF hybrid retrieval over opted-in attendee vectors and exact technology terms.”
3. Open **Why this match?** and identify source evidence.
4. Create an asynchronous intro and show the expiring QR acceptance screen.
5. Show the same query in the Elastic UI / Agent Builder MCP host.
6. Close with the Dash0 trace and Radar deployment evidence, then point to Gitar-reviewed PRs.

## Honest fallback

If the judge’s question is too niche or live signups are sparse, say so and use this seeded, clearly marked question:

> “Our Kubernetes application is slow and we cannot locate the bottleneck.”

It should retrieve the opt-in observability/SRE profile and, only if sponsor intent is selected, the relevant sponsor office-hours card.
