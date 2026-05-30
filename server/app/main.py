"""FastAPI reference implementation for BAC private anchors."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from bac.core.anchor import (
    SERVICE_NAME,
    signing_payload_for_receipt,
    validate_anchor_request,
)
from bac.core.canonicalize import canonical_bytes, canonical_json
from bac.core.hash_chain import hash_json
from server.app.core.config import load_settings
from server.app.db.session import connect
from server.app.signing.keys import load_signing_key


def create_app() -> FastAPI:
    settings = load_settings()
    signing_key = load_signing_key(settings)
    db = connect(settings.db_url)
    lock = Lock()
    _ensure_signing_key(db, signing_key.key_id, signing_key.public_key_b64)

    app = FastAPI(title="BAC Anchor Server", version="0.1.0")
    app.state.db = db
    app.state.lock = lock
    app.state.signing_key = signing_key

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        db.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "db": "ok",
            "active_key_id": signing_key.key_id,
            "time": utc_now(),
        }

    @app.get("/api/v1/public-keys")
    def public_keys() -> dict[str, Any]:
        rows = db.execute(
            "SELECT key_id, public_key_b64, status, created_at FROM signing_keys ORDER BY created_at"
        ).fetchall()
        return {
            "format": "bac.anchor.public_keys.v1",
            "service": SERVICE_NAME,
            "keys": [
                {
                    "key_id": row["key_id"],
                    "alg": "Ed25519",
                    "public_key": row["public_key_b64"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        }

    @app.post("/api/v1/anchors")
    async def create_anchor(request: Request) -> dict[str, Any]:
        if int(request.headers.get("content-length", "0") or "0") > 8192:
            raise HTTPException(status_code=413, detail="request body is too large")
        payload = await request.json()
        errors = validate_anchor_request(payload)
        if errors:
            raise HTTPException(status_code=422, detail={"code": "invalid_anchor_request", "errors": errors})
        with lock:
            existing = db.execute(
                """
                SELECT receipt_json FROM anchors
                WHERE anchor_hash = ? AND COALESCE(ledger_id, '') = COALESCE(?, '') AND client_sequence = ?
                """,
                (payload["anchor_hash"], payload.get("ledger_id"), payload["sequence"]),
            ).fetchone()
            if existing:
                return json.loads(existing["receipt_json"])

            server_sequence = _next_server_sequence(db)
            receipt = _sign_receipt(payload, server_sequence)
            db.execute(
                """
                INSERT INTO anchors (
                  receipt_id, anchor_hash, ledger_id, client_sequence, server_sequence,
                  client_created_at, server_created_at, key_id, signature_alg,
                  signature_b64, request_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt["receipt_id"],
                    receipt["anchor_hash"],
                    payload.get("ledger_id"),
                    payload["sequence"],
                    receipt["server_sequence"],
                    payload["client_created_at"],
                    receipt["server_created_at"],
                    receipt["key_id"],
                    receipt["signature_alg"],
                    receipt["signature"],
                    hash_json(payload),
                    canonical_json(receipt),
                ),
            )
            db.commit()
            return receipt

    @app.get("/api/v1/receipts/{receipt_id}")
    def get_receipt(receipt_id: str) -> dict[str, Any]:
        row = db.execute("SELECT receipt_json FROM anchors WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"code": "receipt_not_found"})
        return json.loads(row["receipt_json"])

    @app.get("/api/v1/ledgers/{ledger_id}/receipts")
    def ledger_receipts(ledger_id: str) -> dict[str, Any]:
        if not ledger_id or len(ledger_id) > 128:
            raise HTTPException(status_code=422, detail={"code": "invalid_ledger_id"})
        rows = db.execute(
            "SELECT receipt_json FROM anchors WHERE ledger_id = ? ORDER BY server_sequence",
            (ledger_id,),
        ).fetchall()
        return {"format": "bac.anchor.ledger_receipts.v1", "ledger_id": ledger_id, "receipts": [json.loads(row["receipt_json"]) for row in rows]}

    @app.get("/admin", response_class=HTMLResponse)
    def admin() -> str:
        count = db.execute("SELECT COUNT(*) AS count FROM anchors").fetchone()["count"]
        last = db.execute("SELECT server_created_at FROM anchors ORDER BY server_sequence DESC LIMIT 1").fetchone()
        recent = last["server_created_at"] if last else "none"
        return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>BAC Anchor</title></head>
<body>
<main>
<h1>BAC Anchor</h1>
<p>service: {SERVICE_NAME}</p>
<p>active key: {signing_key.key_id}</p>
<p>receipts: {count}</p>
<p>latest anchor: {recent}</p>
</main>
</body>
</html>"""

    def _sign_receipt(request_payload: dict[str, Any], server_sequence: int) -> dict[str, Any]:
        created_at = utc_now()
        receipt = {
            "format": "bac.anchor.receipt.v1",
            "anchor_hash": request_payload["anchor_hash"],
            "server_created_at": created_at,
            "service": SERVICE_NAME,
            "key_id": signing_key.key_id,
            "signature_alg": "Ed25519",
            "receipt_id": f"bac_receipt_{created_at.replace('-', '').replace(':', '')}_{uuid.uuid4().hex[:16]}",
            "sequence": request_payload["sequence"],
            "server_sequence": server_sequence,
        }
        signature = signing_key.private_key.sign(canonical_bytes(signing_payload_for_receipt(receipt)))
        receipt["signature"] = base64.b64encode(signature).decode("ascii")
        return receipt

    return app


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_signing_key(db: Any, key_id: str, public_key_b64: str) -> None:
    row = db.execute("SELECT key_id FROM signing_keys WHERE key_id = ?", (key_id,)).fetchone()
    if row:
        return
    db.execute("UPDATE signing_keys SET status = 'retired', retired_at = ? WHERE status = 'active'", (utc_now(),))
    db.execute(
        "INSERT INTO signing_keys (key_id, public_key_b64, status, created_at) VALUES (?, ?, 'active', ?)",
        (key_id, public_key_b64, utc_now()),
    )
    db.commit()


def _next_server_sequence(db: Any) -> int:
    row = db.execute("SELECT COALESCE(MAX(server_sequence), 0) + 1 AS next_sequence FROM anchors").fetchone()
    return int(row["next_sequence"])


app = create_app()
