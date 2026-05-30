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

## API

- `GET /healthz`
- `GET /api/v1/public-keys`
- `POST /api/v1/anchors`
- `GET /api/v1/receipts/{receipt_id}`
- `GET /api/v1/ledgers/{ledger_id}/receipts`
- `GET /admin`

Receipt signatures use Ed25519 over canonical JSON signing payload `bac.anchor.receipt.signing_payload.v1`. Repeating the same `(anchor_hash, ledger_id, sequence)` returns the same receipt.

## Privacy Boundary

Anchor requests must contain only:

- `format`
- blinded `anchor_hash`
- `client_created_at`
- optional pseudonymous `ledger_id`
- optional `ledger_public_key`
- `sequence`

Requests containing private fields such as `path`, `diff`, `payload`, `prompt`, `actor`, `project`, `repo`, or raw `head_hash` are rejected.
