#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${BAC_DEPLOY_DIR:-${REPO_ROOT}/docs/deploy}"

cd "${DEPLOY_DIR}"
if [[ $# -eq 0 ]]; then
  docker compose logs -f --tail="${TAIL:-200}" bac-anchor-app
else
  docker compose logs -f --tail="${TAIL:-200}" "$@"
fi
