"""Least-privilege Elasticsearch API keys for the API, the worker and the Agent Builder caller.

The admin key in .env stays local for bootstrap/provisioning scripts. Deployed pods get only:

  network-api     read/write on this schema's NETwork indices, plus inference (People view colours)
  network-worker  read/write on the same indices, no inference calls, no Kibana, no contacts key
  network-agent   read on the public card index (ES|QL tools) plus Agent Builder in Kibana

Default is a dry run that prints the role descriptors (no secrets). With --apply it creates the
keys (additive: nothing is deleted or reindexed), verifies none of them can manage security or
touch other indices, and writes gitignored .env.api and .env.worker for scripts/deploy_k8s.sh.
If the cluster refuses derived keys from an API key, create the same keys in Kibana
(Stack Management > API keys) with the printed descriptors and pass them via --from-env.

    python -m scripts.provision_keys                 # print descriptors
    python -m scripts.provision_keys --apply         # create keys, write .env.api / .env.worker
    python -m scripts.provision_keys --from-env      # keys already made in Kibana: read NETWORK_API_KEY,
                                                     # NETWORK_WORKER_KEY, NETWORK_AGENT_KEY from the environment or .env
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from app.config import get_settings

# The worker gets only these; the API gets all of .env with the admin key replaced.
WORKER_KEEP = {"ELASTICSEARCH_URL", "ELASTICSEARCH_INDEX_PREFIX", "ELASTIC_SCHEMA_VERSION", "ELASTIC_INFERENCE_ID",
               "ELASTICSEARCH_VERIFY_CERTS", "DEMO_EVENT_ID", "ENVIRONMENT", "PROFILE_EXPIRY_HOURS", "SESSION_HOURS",
               "DRAFT_MINUTES", "WORKER_POLL_SECONDS", "WORKER_LEASE_SECONDS", "WORKER_MAX_ATTEMPTS",
               "DASH0_OTLP_HTTP_BASE", "DASH0_AUTH_TOKEN", "DASH0_DATASET", "OTEL_EXPORTER_OTLP_ENDPOINT",
               "OTEL_EXPORTER_OTLP_HEADERS", "APP_VERSION", "PUBLIC_BASE_URL", "CORS_ORIGINS"}


def descriptors(settings) -> dict[str, dict]:
    pattern = f"{settings.elasticsearch_index_prefix}_*_{settings.elastic_schema_version}*"
    data = {"names": [pattern], "privileges": ["read", "write", "view_index_metadata"], "allow_restricted_indices": False}
    public = {"names": [settings.index("user_vectors") + "*"], "privileges": ["read", "view_index_metadata"]}
    return {
        "network-api": {"network_api": {"cluster": ["monitor_inference"], "indices": [data]}},
        "network-worker": {"network_worker": {"cluster": [], "indices": [data]}},
        "network-agent": {"network_agent": {
            "cluster": ["monitor_inference"], "indices": [public],
            "applications": [{"application": "kibana-.kibana", "privileges": ["feature_agentBuilder.read", "feature_actions.read"],
                              "resources": ["*"]}]}},
    }


def verify(es: str, key: str, settings) -> None:
    """Refuse a key that can manage security/API keys or write outside the NETwork indices."""
    headers = {"Authorization": f"ApiKey {key}"}
    body = {"cluster": ["manage_security", "manage_api_key", "manage", "all"],
            "index": [{"names": ["unrelated-index", ".kibana"], "privileges": ["write", "read"]},
                      {"names": [settings.index("user_vectors")], "privileges": ["read"]}]}
    result = httpx.post(es + "/_security/user/_has_privileges", headers=headers, json=body, timeout=30).raise_for_status().json()
    if any(result["cluster"].values()):
        raise SystemExit("A scoped key can still manage the cluster or security; not writing env files")
    for name in ("unrelated-index", ".kibana"):
        if any(result["index"][name].values()):
            raise SystemExit("A scoped key can reach indices outside NETwork; not writing env files")
    if not result["index"][settings.index("user_vectors")]["read"]:
        raise SystemExit("A scoped key cannot read the public card index")


def env_lines(path: Path) -> list[tuple[str, str]]:
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            pairs.append((name.strip(), value))
    return pairs


def write_env(path: Path, pairs: list[tuple[str, str]], overrides: dict[str, str]) -> None:
    merged = dict(pairs)
    merged.update(overrides)
    path.write_text("".join(f"{name}={value}\n" for name, value in merged.items()), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="create the keys with the admin key in .env")
    parser.add_argument("--from-env", action="store_true", help="use NETWORK_API_KEY / NETWORK_WORKER_KEY / NETWORK_AGENT_KEY")
    parser.add_argument("--expiration", default="30d")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.elastic_enabled:
        raise SystemExit("ELASTICSEARCH_URL and the admin ELASTICSEARCH_API_KEY must be set in .env")
    plan = descriptors(settings)
    if not (args.apply or args.from_env):
        print(json.dumps(plan, indent=2))
        print("\nDry run. Re-run with --apply to create these keys (nothing existing is changed).", file=sys.stderr)
        return
    es = settings.elasticsearch_url.rstrip("/")
    keys: dict[str, str] = {}
    if args.from_env:
        for name, variable in (("network-api", "NETWORK_API_KEY"), ("network-worker", "NETWORK_WORKER_KEY"), ("network-agent", "NETWORK_AGENT_KEY")):
            keys[name] = os.environ.get(variable) or dict(env_lines(Path(".env"))).get(variable, "").strip() or sys.exit(f"Set {variable} in .env")
    else:
        admin = {"Authorization": f"ApiKey {settings.elasticsearch_api_key}"}
        for name, roles in plan.items():
            response = httpx.post(es + "/_security/api_key", headers=admin, timeout=30, json={
                "name": name, "expiration": args.expiration, "role_descriptors": roles,
                "metadata": {"application": "network-concierge", "event": settings.demo_event_id, "component": name}})
            if response.status_code != 200:
                reason = (response.json().get("error") or {}).get("reason", "") if response.headers.get("content-type", "").startswith("application/json") else ""
                raise SystemExit(f"Elasticsearch refused to create {name} ({response.status_code}: {reason[:300]}). Create it in Kibana "
                                 "with the descriptors from a dry run, then use --from-env.")
            keys[name] = response.json()["encoded"]
            print(f"created {name} (id {response.json()['id']}, expires in {args.expiration})")
    for key in keys.values():
        verify(es, key, settings)
    # Scoped keys read by --from-env stay out of both files: each pod gets only its own key (Gitar review).
    scoped = {"NETWORK_API_KEY", "NETWORK_WORKER_KEY", "NETWORK_AGENT_KEY"}
    source = [(name, value) for name, value in env_lines(Path(".env")) if name not in scoped]
    write_env(Path(".env.api"), source,
              {"ELASTICSEARCH_API_KEY": keys["network-api"], "AGENT_BUILDER_API_KEY": keys["network-agent"], "NETWORK_ROLE": "api"})
    write_env(Path(".env.worker"), [(n, v) for n, v in source if n in WORKER_KEEP],
              {"ELASTICSEARCH_API_KEY": keys["network-worker"], "NETWORK_ROLE": "worker", "OTEL_SERVICE_NAME": "network-worker"})
    print("verified: no key can manage security or reach non-NETwork indices")
    print("wrote .env.api and .env.worker (gitignored); deploy with scripts/deploy_k8s.sh")
    print("After the rollout is healthy, rotate the admin key in Kibana; keep it out of every pod.")


if __name__ == "__main__":
    main()
