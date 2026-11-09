#!/usr/bin/env bash
# Deploy NETwork (API + worker) to the current kubectl context and connect it to Radar Cloud.
#
#   RADAR_TOKEN=rhc_... RADAR_CLUSTER=<cluster-id> scripts/deploy_k8s.sh
#
# Secrets are read from the local, gitignored .env and stored only as Kubernetes Secrets.
# IMAGE defaults to the locally built network-concierge:dev (Docker Desktop shares its image store).
set -euo pipefail
cd "$(dirname "$0")/.."

NS="${NS:-network}"
IMAGE_REPO="${IMAGE_REPO:-network-concierge}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
PULL_POLICY="${PULL_POLICY:-Never}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:8080}"
HELM="${HELM:-$(command -v helm || echo .tools/helm/windows-amd64/helm.exe)}"

[ -f .env ] || { echo "Missing .env" >&2; exit 1; }

# 1. Application secrets (API and worker get separate Secret objects, as the chart expects).
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
grep -E '^[A-Z0-9_]+=' .env | grep -vE '^(PUBLIC_BASE_URL|CORS_ORIGINS|OTEL_SERVICE_NAME)=' > "$env_file"
printf 'PUBLIC_BASE_URL=%s\nCORS_ORIGINS=%s\nOTEL_SERVICE_NAME=network-api\n' "$PUBLIC_BASE_URL" "$PUBLIC_BASE_URL" >> "$env_file"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
for secret in network-concierge-secrets network-worker-secrets; do
  kubectl create secret generic "$secret" -n "$NS" --from-env-file="$env_file" --dry-run=client -o yaml | kubectl apply -f -
done

# 2. Application chart.
"$HELM" upgrade --install network deploy/helm/network-concierge -n "$NS" \
  --set image.repository="$IMAGE_REPO" --set image.tag="$IMAGE_TAG" --set image.pullPolicy="$PULL_POLICY" \
  --wait --timeout 5m
# Pick up a rebuilt image with the same tag.
kubectl -n "$NS" rollout restart deploy/network-network-concierge deploy/network-network-concierge-worker
kubectl -n "$NS" rollout status deploy/network-network-concierge --timeout=180s
kubectl -n "$NS" get pods -o wide

# 3. Radar agent (Skyhook chart), connected to Radar Cloud.
if [ -n "${RADAR_TOKEN:-}" ]; then
  "$HELM" repo add skyhook https://skyhook-io.github.io/helm-charts >/dev/null 2>&1 || true
  "$HELM" repo update skyhook
  kubectl create namespace radar --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic radar-cloud-config -n radar --from-literal=token="$RADAR_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
  "$HELM" upgrade --install radar skyhook/radar -n radar \
    --set cloud.enabled=true \
    --set cloud.url='wss://api.radarhq.io/agent' \
    --set cloud.insecureSkipVerify=false \
    --set cloud.clusterName="${RADAR_CLUSTER:?Set RADAR_CLUSTER}" \
    --set cloud.existingSecret=radar-cloud-config \
    --set auth.mode=proxy \
    --set rbac.helm=true --set rbac.secrets=true --set rbac.podExec=true \
    --set rbac.portForward=true --set rbac.metrics=true --set rbac.selfUpgrade=true
  kubectl -n radar get pods
else
  echo "RADAR_TOKEN not set; skipped Radar agent install"
fi

echo "Port-forward for the demo:  kubectl -n $NS port-forward svc/network-network-concierge 8080:8000"
