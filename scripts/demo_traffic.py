"""Generate labeled synthetic demo traffic so Dash0 shows search/RAG traces and latency metrics.

Usage: python -m scripts.demo_traffic [BASE_URL] [ROUNDS]

Creates one clearly labeled synthetic card (no real person, reserved example.com contact),
waits for the worker to index it, runs fixed demo questions, then revokes the card.
Requires the worker to be running against the same Elasticsearch project.
"""
import sys
import time

import httpx

QUESTIONS = [
    "Who can help us diagnose Kubernetes latency before the next session?",
    "Looking for OpenTelemetry and observability experience",
    "Vector search and hybrid retrieval with Elasticsearch",
    "Machine learning systems and evaluation",
]
PROBE = {"display_name": "DEMO Telemetry Probe", "consent_to_discover": True, "roles": ["peer"],
         "headline": "Labeled synthetic load probe; revoked after the run",
         "public_summary": "Synthetic card used only to generate observability traffic.",
         "can_help_with": ["Load testing"], "looking_for": ["Kubernetes", "Observability"], "email": "probe@example.com"}


def main(base: str = "http://127.0.0.1:8000", rounds: int = 3) -> None:
    with httpx.Client(base_url=base.rstrip("/"), headers={"X-NETwork-Request": "1"}, timeout=90) as client:
        client.get("/healthz").raise_for_status()
        print("readyz", client.get("/readyz").status_code)
        client.post("/api/session").raise_for_status()
        created = client.post("/api/profiles", json=PROBE)
        created.raise_for_status()
        try:
            for _ in range(60):
                state = client.get("/api/me").json().get("state")
                if state in {"indexed", "failed"}:
                    break
                time.sleep(2)
            print("probe card", state)
            if state != "indexed":
                raise SystemExit("Probe card was not indexed; is the worker running?")
            for i in range(rounds):
                for question in QUESTIONS:
                    started = time.perf_counter()
                    search = client.post("/api/search", json={"question": question, "size": 5})
                    compare = client.post("/api/search/compare", json={"question": question, "size": 5})
                    print(f"search {search.status_code} compare {compare.status_code} {1000 * (time.perf_counter() - started):.0f}ms")
                ask = client.post("/api/ask", json={"question": QUESTIONS[i % len(QUESTIONS)], "sponsor_intent": i % 2 == 0})
                print("ask", ask.status_code, ask.json().get("mode") if ask.status_code == 200 else ask.text[:120])
        finally:
            print("revoke", client.delete("/api/me").status_code)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000", int(sys.argv[2]) if len(sys.argv) > 2 else 3)
