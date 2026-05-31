#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${BAC_DEPLOY_DIR:-${REPO_ROOT}/docs/deploy}"

cd "${DEPLOY_DIR}"

mkdir -p backups
backup_file="backups/bac-anchor-$(date +%Y%m%d-%H%M%S).sql"

docker compose exec -T bac-anchor-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$backup_file"

echo "$backup_file"
