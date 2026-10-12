#!/usr/bin/env bash
# Starts the GPU tunnel (if it isn't already up) and the Private Doc Agent web app.
set -euo pipefail
cd "$(dirname "$0")"

KUBECONFIG_FILE="${KUBECONFIG_FILE:-$HOME/.kube/s09.yaml}"
PORT="${PORT:-5050}"

if ! curl -s --max-time 3 http://localhost:8000/v1/models >/dev/null; then
  echo "Connecting to the workshop GPU..."
  KUBECONFIG="$KUBECONFIG_FILE" kubectl port-forward svc/vllm 8000:8000 >/tmp/vllm-port-forward.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s --max-time 2 http://localhost:8000/v1/models >/dev/null && break
    sleep 1
  done
fi

echo "Open http://localhost:${PORT}"
PORT="$PORT" exec python3 -m app.server
