# BAC Anchor Server

`server/` contains the reference private-anchor service for BAC. It stores only blinded `anchor_hash` values plus receipt metadata; it does not receive `.bac` contents, project names, file paths, diffs, prompts, actors, repositories, or raw `head_hash` values.

## Run Locally

```bash
python -m pip install -e ".[server]"
BAC_ANCHOR_ENV=development \
BAC_ANCHOR_DB_URL=sqlite:///./tmp/bac-anchor.sqlite3 \
uvicorn server.app.main:app --reload --port 8080
```

Health check:

```bash
curl http://localhost:8080/healthz
```

## Docker

```bash
docker compose -f server/docker-compose.yml up --build
```

The compose file uses SQLite in a named volume and development key generation for local smoke tests. For production, set `BAC_ANCHOR_ENV=production` and provide an Ed25519 private key through `BAC_ANCHOR_PRIVATE_KEY_PATH` or `BAC_ANCHOR_PRIVATE_KEY_B64`.

For server deployments, use [`docs/deploy`](../docs/deploy/README.md). That compose package runs the published DockerHub image as `bac-anchor-app`, stores anchor data in PostgreSQL, uses Redis for production rate-limit state, and joins the external `npm_default` network for reverse proxying. Repository helper scripts for that deployment live in [`tools`](../tools), outside the copyable deployment config.

Production deployments must also configure bearer tokens:

```bash
BAC_ANCHOR_API_TOKEN=change-me-write-token
BAC_ANCHOR_ADMIN_TOKEN=change-me-admin-token
BAC_ANCHOR_DB_URL=postgresql://bac_anchor:change-me@bac-anchor-postgres:5432/bac_anchor
BAC_ANCHOR_REDIS_URL=redis://bac-anchor-redis:6379/0
BAC_ANCHOR_ENABLE_LEDGER_QUERY=false
BAC_ANCHOR_MAX_BODY_BYTES=8192
BAC_ANCHOR_RATE_LIMIT_PER_MINUTE=120
```

Use a reverse proxy or platform gateway for additional network-level rate limits and TLS termination. Do not store real tokens or private keys in the repository.

End users can register or log in through the CLI or the small web console:

```bash
bac cloud register --url https://bac.example.com --email user@example.com
bac cloud login --url https://bac.example.com --email user@example.com
bac cloud link --url https://bac.example.com --ledger-name my-project
```

The web console is available at `/cloud`. User tokens are bearer tokens for client writes and ledger management. The server still keeps the Ed25519 signing private key; clients only store their own cloud token outside `.bac`.

To publish the server image directly to DockerHub as `linux/amd64`, use the local release script:

```bash
make dockerhub-publish
```

See [DockerHub Release](../docs/dockerhub-release.md) for Docker login, tag rules, and safety checks.

## API

- `GET /healthz`
- `GET /cloud`
- `GET /api/v1/public-keys`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/cloud/me` requires a user bearer token
- `POST /api/v1/cloud/ledgers` requires a user bearer token
- `POST /api/v1/anchors` requires `Authorization: Bearer ...` in production; either the deployment write token or a registered user token is accepted
- `GET /api/v1/receipts/{receipt_id}` remains public for callers that already know an opaque receipt id
- `GET /api/v1/ledgers/{ledger_id}/receipts` is disabled by default in production; when enabled, it requires the deployment write token or the owning user token
- `GET /admin` requires `Authorization: Bearer $BAC_ANCHOR_ADMIN_TOKEN` in production, or returns 404 if no admin token is configured

Receipt signatures use Ed25519 over canonical JSON signing payload `bac.anchor.receipt.signing_payload.v1`. Repeating the same `(anchor_hash, ledger_id, sequence)` returns the same receipt.

Request bodies are bounded by `BAC_ANCHOR_MAX_BODY_BYTES`. Production anchor writes are additionally guarded by an in-process per-client rate limit suitable for the reference server. Client-side `bac anchor push` can send the production write token with `--token` or `BAC_ANCHOR_API_TOKEN`; do not store tokens in `.bac`.

## Privacy Boundary

Anchor requests must contain only:

- `format`
- blinded `anchor_hash`
- `client_created_at`
- optional pseudonymous `ledger_id`
- optional `ledger_public_key`
- `sequence`

Requests containing private fields such as `path`, `diff`, `payload`, `prompt`, `actor`, `project`, `repo`, or raw `head_hash` are rejected.
