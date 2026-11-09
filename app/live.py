"""In-process live request telemetry for the 2D map and Stack tab.

Records route templates only (never concrete paths, so tokens and ids stay out), status and
latency for the last few minutes. The same requests are exported to Dash0 as OTLP spans;
this ring buffer is what the browser can see without a Dash0 key.
"""
import time
from collections import deque

WINDOW = 300
BUCKET = 5
_events: deque = deque(maxlen=5000)
_seq = 0


def record(method: str, route: str, status: int, ms: float) -> None:
    global _seq
    _seq += 1
    _events.append((time.time(), _seq, method, route, status, ms))


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 1)


def snapshot(since: int = 0) -> dict:
    now = time.time()
    while _events and _events[0][0] < now - WINDOW:
        _events.popleft()
    events = list(_events)
    routes: dict[str, dict] = {}
    for _, _, method, route, status, ms in events:
        item = routes.setdefault(f"{method} {route}", {"route": f"{method} {route}", "count": 0, "errors": 0, "ms": []})
        item["count"] += 1
        item["errors"] += status >= 400
        item["ms"].append(ms)
    table = sorted(({"route": r["route"], "count": r["count"], "errors": r["errors"], "p50": _pct(r["ms"], .5), "p95": _pct(r["ms"], .95)}
                    for r in routes.values()), key=lambda r: -r["count"])
    buckets = [0] * 24
    for t, *_ in events:
        age = now - t
        if age < BUCKET * 24:
            buckets[23 - int(age // BUCKET)] += 1
    recent = [{"seq": seq, "method": method, "route": route, "status": status, "ms": round(ms, 1), "ago": round(now - t, 2)}
              for t, seq, method, route, status, ms in events if seq > since][-40:]
    all_ms = [e[5] for e in events]
    return {"window_seconds": WINDOW, "bucket_seconds": BUCKET, "series": [round(b / BUCKET, 2) for b in buckets],
            "total": len(events), "rps": round(sum(buckets[-2:]) / (2 * BUCKET), 2), "p50": _pct(all_ms, .5), "p95": _pct(all_ms, .95),
            "errors": sum(1 for e in events if e[4] >= 400), "routes": table[:12], "recent": recent, "seq": _seq}
