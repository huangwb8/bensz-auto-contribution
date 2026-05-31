#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing docs/deploy/.env. Copy .env.example to .env and fill production secrets." >&2
  exit 1
fi

if ! docker network inspect npm_default >/dev/null 2>&1; then
  echo "Missing Docker network: npm_default. Create it or start Nginx Proxy Manager first." >&2
  exit 1
fi

docker compose pull
docker compose up -d --remove-orphans
docker compose ps
