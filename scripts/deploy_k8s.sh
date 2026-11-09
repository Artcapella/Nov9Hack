#!/usr/bin/env bash
# Deploy NETwork (API + worker) to the current kubectl context and connect it to Radar Cloud.
#
#   python -m scripts.provision_keys --apply      # least-privilege Elastic keys -> .env.api / .env.worker
#   RADAR_TOKEN=rhc_... RADAR_CLUSTER=<cluster-id> CLIENT_IP_HEADER=cf-connecting-ip scripts/deploy_k8s.sh
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

# 1. Application secrets: one Secret per component, each holding only what that process needs.
#    scripts/provision_keys.py writes .env.api / .env.worker with least-privilege Elastic keys. Without them
#    the admin key from .env would be deployed, so refuse unless explicitly allowed for a local cluster.
if [ -f .env.api ] && [ -f .env.worker ]; then
  api_source=.env.api; worker_source=.env.worker
elif [ "${ALLOW_ADMIN_KEY:-}" = "1" ]; then
  echo "WARNING: deploying the admin Elastic key from .env (ALLOW_ADMIN_KEY=1)" >&2
  api_source=.env; worker_source=.env
else
  echo "Run 'python -m scripts.provision_keys --apply' first (or ALLOW_ADMIN_KEY=1 for a throwaway cluster)" >&2
  exit 1
fi
api_env="$(mktemp)"; worker_env="$(mktemp)"
trap 'rm -f "$api_env" "$worker_env"' EXIT
EDGE="PUBLIC_BASE_URL|CORS_ORIGINS|OTEL_SERVICE_NAME|NETWORK_ROLE|CLIENT_IP_HEADER"
grep -E '^[A-Z0-9_]+=' "$api_source" | grep -vE "^($EDGE)=" > "$api_env"
# The worker never decrypts contacts, serves the ops view or calls Agent Builder.
grep -E '^[A-Z0-9_]+=' "$worker_source" | grep -vE "^($EDGE|CONTACT_ENCRYPTION_KEY|OPS_TOKEN|AGENT_BUILDER_[A-Z_]+|KIBANA_URL)=" > "$worker_env"
printf 'PUBLIC_BASE_URL=%s\nCORS_ORIGINS=%s\nOTEL_SERVICE_NAME=network-api\nNETWORK_ROLE=api\n' "$PUBLIC_BASE_URL" "$PUBLIC_BASE_URL" >> "$api_env"
# Behind a Cloudflare tunnel the edge overwrites cf-connecting-ip, so rate limits see real clients.
[ -n "${CLIENT_IP_HEADER:-}" ] && printf 'CLIENT_IP_HEADER=%s\n' "$CLIENT_IP_HEADER" >> "$api_env"
printf 'PUBLIC_BASE_URL=%s\nCORS_ORIGINS=%s\nOTEL_SERVICE_NAME=network-worker\nNETWORK_ROLE=worker\n' "$PUBLIC_BASE_URL" "$PUBLIC_BASE_URL" >> "$worker_env"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic network-concierge-secrets -n "$NS" --from-env-file="$api_env" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic network-worker-secrets -n "$NS" --from-env-file="$worker_env" --dry-run=client -o yaml | kubectl apply -f -

# Docker Desktop's kind-mode node does not share the Docker image store; import the local image.
if [ "$PULL_POLICY" = "Never" ] && docker inspect desktop-control-plane >/dev/null 2>&1; then
  docker save "$IMAGE_REPO:$IMAGE_TAG" | docker exec -i desktop-control-plane ctr -n k8s.io images import - >/dev/null
fi

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
    --set rbac.helm=true --set rbac.metrics=true --set rbac.selfUpgrade=true \
    --set rbac.secrets=false --set rbac.podExec=false --set rbac.portForward=false
  # Least privilege: no pod exec or port-forward into pods holding CONTACT_ENCRYPTION_KEY and API keys.
  # In cloud mode the chart always keeps a Secret *cache* grant on the agent ServiceAccount, but every
  # Secret read is re-checked against the requesting Radar user's own RBAC (owner->admin, member->edit,
  # viewer->view). Invite judges/guests as Radar *viewers* only; health, events and metrics stay visible.
  kubectl -n radar get pods
else
  echo "RADAR_TOKEN not set; skipped Radar agent install"
fi

echo "Port-forward for the demo:  kubectl -n $NS port-forward svc/network-network-concierge 8080:8000"
