#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ $# -eq 0 ]]; then
  docker compose logs -f --tail="${TAIL:-200}" bac-anchor-app
else
  docker compose logs -f --tail="${TAIL:-200}" "$@"
fi
