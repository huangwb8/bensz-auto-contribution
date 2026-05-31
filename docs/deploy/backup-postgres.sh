#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p backups
backup_file="backups/bac-anchor-$(date +%Y%m%d-%H%M%S).sql"

docker compose exec -T bac-anchor-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$backup_file"

echo "$backup_file"
