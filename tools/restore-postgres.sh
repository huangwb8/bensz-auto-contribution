#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${BAC_DEPLOY_DIR:-${REPO_ROOT}/docs/deploy}"

cd "${DEPLOY_DIR}"

if [[ $# -ne 1 ]]; then
  echo "Usage: tools/restore-postgres.sh backups/bac-anchor-YYYYMMDD-HHMMSS.sql" >&2
  exit 1
fi

backup_file="$1"
if [[ ! -f "$backup_file" ]]; then
  echo "Backup file not found: $backup_file" >&2
  exit 1
fi

docker compose exec -T bac-anchor-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  < "$backup_file"
