"""Per-client request budgets for the public event URL.

Token buckets keyed by session (hashed cookie) or, before a session exists, by client IP.
In-process state: correct for the single API replica the chart deploys. Costly routes
(inference, Agent Builder, resume parsing, card creation) get their own, tighter budgets
on top of a general per-client budget. Rejections are counted in Dash0 by rule name only.
"""
import hashlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    methods: frozenset[str]
    pattern: re.Pattern
    per_minute: float
    burst: int
    by_ip: bool = False


def rule(name, methods, pattern, per_minute, burst, by_ip=False):
    return Rule(name, frozenset(methods), re.compile(pattern), per_minute, burst, by_ip)


RULES = (
    # Sessions are created before any cookie exists, so they are budgeted per client IP.
    rule("session", {"POST"}, r"^/api/session$", 30, 60, by_ip=True),
    rule("profile", {"POST"}, r"^/api/profiles$", 6, 6),
    rule("resume", {"POST"}, r"^/api/resumes/preview$", 4, 4),
    rule("ask", {"POST"}, r"^/api/ask$", 6, 6),
    rule("compare", {"POST"}, r"^/api/search/compare$", 15, 15),
    rule("search", {"POST"}, r"^/api/(search|people/search|talks/search)$", 30, 30),
    rule("interaction", {"POST"}, r"^/api/interactions$", 60, 30),
    rule("introduction", {"POST"}, r"^/api/connection-requests$", 10, 10),
    rule("break", {"POST", "DELETE"}, r"^/api/me/break$", 20, 20),
    rule("availability", {"POST"}, r"^/api/me/availability$", 20, 20),
    rule("ops", {"GET"}, r"^/api/ops/", 30, 30, by_ip=True),
)
GENERAL = rule("general", {"GET", "POST", "PUT", "PATCH", "DELETE"}, r"^/api/", 300, 120)


class RateLimiter:
    def __init__(self, max_keys: int = 20000):
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.max_keys = max_keys

    def take(self, key: str, per_minute: float, burst: int) -> float:
        """Spend one token. Returns 0 when allowed, else seconds until a token is available."""
        rate, now = per_minute / 60, time.monotonic()
        tokens, at = self.buckets.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - at) * rate)
        allowed = tokens >= 1
        self.buckets[key] = (tokens - 1 if allowed else tokens, now)
        self.buckets.move_to_end(key)
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)
        return 0.0 if allowed else (1 - tokens) / rate

    def check(self, method: str, path: str, client_ip: str, session_token: str | None) -> tuple[str, float] | None:
        """Returns (rule name, retry-after seconds) for the first exhausted budget, else None."""
        client = "s:" + hashlib.sha256(session_token.encode()).hexdigest()[:32] if session_token else "ip:" + client_ip
        for item in (*(r for r in RULES if method in r.methods and r.pattern.search(path)), GENERAL):
            if not (method in item.methods and item.pattern.search(path)):
                continue
            key = f"{item.name}|{'ip:' + client_ip if item.by_ip else client}"
            wait = self.take(key, item.per_minute, item.burst)
            if wait:
                return item.name, wait
        return None
