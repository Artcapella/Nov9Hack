"""Local stdio MCP fallback. Uses a participant session and the API's boundaries.

The native Elastic MCP endpoint remains the preferred coding-host integration.
Do not expose this fallback as an unauthenticated remote HTTP service.
"""
import os

import httpx
from fastmcp import FastMCP

app = FastMCP("NETwork scoped tools")


async def request_api(path: str, body: dict | None = None):
    token = os.environ.get("NETWORK_SESSION_TOKEN")
    if not token:
        raise ValueError("Use a local participant session via NETWORK_SESSION_TOKEN")
    base = os.environ.get("NETWORK_API_URL", "http://localhost:8000").rstrip("/")
    if not base.startswith("https://") and base not in {"http://localhost:8000", "http://127.0.0.1:8000"}:
        raise ValueError("Remote API connections require HTTPS")
    async with httpx.AsyncClient(base_url=base, cookies={"network_session": token},
                                  headers={"X-NETwork-Request": "1"}, timeout=60) as client:
        response = await client.post(path, json=body) if body is not None else await client.get(path)
        if response.status_code >= 400:
            raise ValueError("NETwork request denied or unavailable")
        return response.json()


@app.tool()
async def find_conference_expert(question: str, limit: int = 5) -> list[dict]:
    """Search approved event attendees. Never returns contacts or hidden resume text."""
    return await request_api("/api/search", {"question": question, "size": max(1, min(limit, 10))})


@app.tool()
async def find_sponsors(question: str, limit: int = 5) -> list[dict]:
    """Explicitly include approved sponsor/recruiter cards for this event."""
    results = await request_api("/api/search", {"question": question, "size": max(1, min(limit, 10)),
                                               "roles": ["sponsor", "recruiter"], "sponsor_intent": True})
    return results


if __name__ == "__main__":
    app.run()
