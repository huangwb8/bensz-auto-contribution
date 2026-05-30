#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${IMAGE:-huangwb8/bensz-auto-contribution}"
VERSION="${VERSION:-}"
PUSH="${PUSH:-1}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
SKIP_TESTS="${SKIP_TESTS:-0}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
VERIFY_PULL="${VERIFY_PULL:-1}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

warn() {
  echo "WARN: $*" >&2
}

info() {
  echo "==> $*"
}

is_true() {
  [[ "${1}" == "1" || "${1}" == "true" || "${1}" == "TRUE" || "${1}" == "yes" || "${1}" == "YES" ]]
}

run_cmd() {
  if is_true "${DRY_RUN}"; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi

  "$@"
}

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing required command: ${command_name}"
}

read_version() {
  if [[ -n "${VERSION}" ]]; then
    return 0
  fi

  VERSION="$(
    cd "${REPO_ROOT}" && python - <<'PY'
import re
from pathlib import Path

text = Path("pyproject.toml").read_text(encoding="utf-8")

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

if tomllib is not None:
    data = tomllib.loads(text)
    print(data["project"]["version"])
else:
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if stripped.startswith("[") and in_project:
            break
        if in_project:
            match = re.match(r'^version\s*=\s*"([^"]+)"\s*$', stripped)
            if match:
                print(match.group(1))
                break
    else:
        raise SystemExit("project.version not found in pyproject.toml")
PY
  )"
}

validate_flags() {
  case "${PUSH}" in
    0|1) ;;
    *) fail "PUSH must be 0 or 1" ;;
  esac

  case "${DRY_RUN}" in
    0|1) ;;
    *) fail "DRY_RUN must be 0 or 1" ;;
  esac

  case "${FORCE}" in
    0|1) ;;
    *) fail "FORCE must be 0 or 1" ;;
  esac

  case "${SKIP_TESTS}" in
    0|1) ;;
    *) fail "SKIP_TESTS must be 0 or 1" ;;
  esac

  [[ -n "${IMAGE}" ]] || fail "IMAGE is empty"
  [[ "${VERSION}" != v* ]] || fail "VERSION must not include the v prefix: ${VERSION}"
  [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] || \
    fail "VERSION must match x.y.z or x.y.z-prerelease: ${VERSION}"
}

require_repo_root() {
  [[ "$(pwd -P)" == "${REPO_ROOT}" ]] || fail "run this script from the repository root: ${REPO_ROOT}"
  [[ -f "${REPO_ROOT}/pyproject.toml" ]] || fail "missing pyproject.toml"
  [[ -f "${REPO_ROOT}/server/Dockerfile" ]] || fail "missing server/Dockerfile"
}

require_docker() {
  require_command docker
  docker buildx version >/dev/null 2>&1 || fail "docker buildx is not available"
}

require_clean_worktree() {
  if is_true "${DRY_RUN}" || [[ "${ALLOW_DIRTY}" == "1" ]]; then
    return 0
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    fail "working tree is dirty; commit/stash changes or set ALLOW_DIRTY=1"
  fi
}

require_docker_login() {
  if [[ "${PUSH}" != "1" ]] || is_true "${DRY_RUN}"; then
    return 0
  fi

  local docker_config="${DOCKER_CONFIG:-${HOME}/.docker}/config.json"
  [[ -f "${docker_config}" ]] || fail "Docker is not logged in; run docker login with a Docker Hub access token"

  if ! grep -Eq '"auths"|"credsStore"|"credHelpers"' "${docker_config}"; then
    fail "Docker login state was not found in ${docker_config}; run docker login first"
  fi
}

require_tag_not_exists() {
  if [[ "${PUSH}" != "1" || "${FORCE}" == "1" ]] || is_true "${DRY_RUN}"; then
    return 0
  fi

  if docker buildx imagetools inspect "${IMAGE}:${VERSION}" >/dev/null 2>&1; then
    fail "image tag already exists: ${IMAGE}:${VERSION} (set FORCE=1 to overwrite)"
  fi
}

is_stable_version() {
  [[ "${VERSION}" != *-* ]]
}

image_tags() {
  local base_version major minor

  printf '%s:%s\n' "${IMAGE}" "${VERSION}"
  if is_stable_version; then
    base_version="${VERSION%%-*}"
    IFS='.' read -r major minor _ <<< "${base_version}"
    printf '%s:latest\n' "${IMAGE}"
    printf '%s:%s.%s\n' "${IMAGE}" "${major}" "${minor}"
    printf '%s:%s\n' "${IMAGE}" "${major}"
  fi
}

run_preflight_checks() {
  if [[ "${SKIP_TESTS}" == "1" ]]; then
    warn "tests were skipped because SKIP_TESTS=1"
    return 0
  fi

  info "Running test suite"
  run_cmd python -m pytest -q
}

build_and_publish() {
  local commit created output_flag tag
  local tags=()
  local args=()

  commit="$(git rev-parse HEAD)"
  created="$(git log -1 --format=%cI HEAD)"
  output_flag="--load"
  [[ "${PUSH}" == "1" ]] && output_flag="--push"

  while IFS= read -r tag; do
    [[ -n "${tag}" ]] || continue
    tags+=("${tag}")
  done < <(image_tags)

  args=(
    docker buildx build
    --platform linux/amd64
    --provenance=false
    -f server/Dockerfile
    --label "org.opencontainers.image.title=bensz-auto-contribution anchor server"
    --label "org.opencontainers.image.source=https://github.com/huangwb8/bensz-auto-contribution"
    --label "org.opencontainers.image.revision=${commit}"
    --label "org.opencontainers.image.version=${VERSION}"
    --label "org.opencontainers.image.created=${created}"
  )

  for tag in "${tags[@]}"; do
    args+=(-t "${tag}")
  done

  args+=("${output_flag}" .)

  info "Building linux/amd64 image"
  run_cmd "${args[@]}"
}

verify_published_image() {
  if [[ "${PUSH}" != "1" ]] || is_true "${DRY_RUN}" || [[ "${VERIFY_PULL}" != "1" ]]; then
    return 0
  fi

  info "Inspecting published image"
  run_cmd docker buildx imagetools inspect "${IMAGE}:${VERSION}"

  info "Pulling published image"
  run_cmd docker pull "${IMAGE}:${VERSION}"

  info "Smoke testing image entrypoint"
  run_cmd docker run --rm --entrypoint python "${IMAGE}:${VERSION}" -c "import server.app.main; print('ok')"
}

main() {
  require_repo_root
  read_version
  validate_flags
  require_command python
  require_command git
  require_docker
  require_clean_worktree
  require_docker_login
  require_tag_not_exists
  run_preflight_checks
  build_and_publish
  verify_published_image

  info "Docker Hub image is ready: ${IMAGE}:${VERSION}"
}

main "$@"
