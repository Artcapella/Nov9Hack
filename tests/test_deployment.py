from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


def test_rendered_deployment_routes_only_to_api_and_mounts_tls_and_worker_secret():
    helm = shutil.which("helm")
    local = Path(".tools/helm/windows-amd64/helm.exe")
    if not helm and local.exists():
        helm = str(local.resolve())
    if not helm:
        pytest.skip("Helm is required for deployment rendering (installed in CI)")
    result = subprocess.run([helm, "template", "network", "deploy/helm/network-concierge", "--namespace", "team-test",
        "--set", "ingress.enabled=true", "--set", "ingress.tls[0].secretName=network-tls",
        "--set", "ingress.tls[0].hosts[0]=network.example.com"], capture_output=True, text=True, check=True)
    resources = list(yaml.safe_load_all(result.stdout))
    service = next(resource for resource in resources if resource["kind"] == "Service")
    api = next(resource for resource in resources if resource["kind"] == "Deployment" and not resource["metadata"]["name"].endswith("-worker"))
    worker = next(resource for resource in resources if resource["kind"] == "Deployment" and resource["metadata"]["name"].endswith("-worker"))
    assert service["spec"]["selector"] == api["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"] != worker["spec"]["template"]["metadata"]["labels"]
    assert worker["spec"]["template"]["spec"]["containers"][0]["envFrom"][0]["secretRef"]["name"] == "network-worker-secrets"
    ingress = next(resource for resource in resources if resource["kind"] == "Ingress")
    assert ingress["spec"]["tls"] == [{"hosts": ["network.example.com"], "secretName": "network-tls"}]
    for deployment in (api, worker):
        spec = deployment["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert not spec["containers"][0]["image"].endswith(":latest")
    assert {"name": "NETWORK_ROLE", "value": "worker"} in worker["spec"]["template"]["spec"]["containers"][0]["env"]
    policies = {r["metadata"]["name"]: r["spec"] for r in resources if r["kind"] == "NetworkPolicy"}
    worker_policy = next(spec for name, spec in policies.items() if name.endswith("-worker"))
    api_policy = next(spec for name, spec in policies.items() if name.endswith("-api"))
    assert worker_policy["ingress"] == [] and api_policy["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8000}]}]
    assert api_policy["podSelector"]["matchLabels"] == api["spec"]["template"]["metadata"]["labels"]
