#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${BAC_DEPLOY_DIR:-${REPO_ROOT}/docs/deploy}"

cd "${DEPLOY_DIR}"

if [[ ! -f .env ]]; then
  echo "Missing ${DEPLOY_DIR}/.env. Copy .env.example to .env and fill production secrets." >&2
  exit 1
fi

if ! docker network inspect npm_default >/dev/null 2>&1; then
  echo "Missing Docker network: npm_default. Create it or start Nginx Proxy Manager first." >&2
  exit 1
fi

docker compose pull
docker compose up -d --remove-orphans
docker compose ps
