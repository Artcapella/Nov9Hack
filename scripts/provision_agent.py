"""Render scoped Agent Builder definitions; --apply provisions with an admin key."""
import argparse
import asyncio
import json

import httpx

from app.agent_tools import agent_definition, definitions
from app.config import get_settings


async def main(apply: bool):
    settings = get_settings()
    tools = definitions(settings)
    agent = agent_definition(settings)
    if not apply:
        print(json.dumps({"tools": tools, "agent": agent}, indent=2))
        return
    if not settings.kibana_url or not settings.agent_builder_api_key:
        raise SystemExit("Set KIBANA_URL and an administrative AGENT_BUILDER_API_KEY for provisioning")
    async with httpx.AsyncClient(timeout=60) as client:
        for endpoint, definition in [("tools", tool) for tool in tools] + [("agents", agent)]:
            response = await client.post(settings.kibana_url.rstrip("/") + "/api/agent_builder/" + endpoint,
                                        headers={"Authorization": "ApiKey " + settings.agent_builder_api_key, "kbn-xsrf": "true"}, json=definition)
            if response.status_code >= 400:
                raise SystemExit(f"Provisioning failed ({response.status_code}); inspect the target Kibana API/version and existing IDs")
            print("Created " + definition["id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
