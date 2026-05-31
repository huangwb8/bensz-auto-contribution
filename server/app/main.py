"""FastAPI reference implementation for BAC private anchors."""

from __future__ import annotations

import base64
import json
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
    rate_limiter = _RateLimiter(settings.rate_limit_per_minute)
    _ensure_signing_key(db, signing_key.key_id, signing_key.public_key_b64)

    app = FastAPI(title="BAC Anchor Server", version="0.1.0")
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes)
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
        _require_token(request, settings, settings.api_token, "api")
        if _is_production(settings) and not rate_limiter.allow(_rate_limit_key(request)):
            raise HTTPException(status_code=429, detail={"code": "rate_limited"})
        payload = await request.json()
        errors = validate_anchor_request(payload)
        if errors:
            raise HTTPException(status_code=422, detail={"code": "invalid_anchor_request", "errors": errors})
        ledger_id = payload.get("ledger_id") or ""
        with lock:
            existing = _find_existing_anchor(db, payload["anchor_hash"], ledger_id, payload["sequence"])
            if existing:
                return json.loads(existing["receipt_json"])

            server_sequence = _next_server_sequence(db)
            receipt = _sign_receipt(payload, server_sequence)
            try:
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
                        ledger_id,
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
            except sqlite3.IntegrityError:
                db.rollback()
                existing = _find_existing_anchor(db, payload["anchor_hash"], ledger_id, payload["sequence"])
                if existing:
                    return json.loads(existing["receipt_json"])
                raise
            return receipt

    @app.get("/api/v1/receipts/{receipt_id}")
    def get_receipt(receipt_id: str) -> dict[str, Any]:
        row = db.execute("SELECT receipt_json FROM anchors WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"code": "receipt_not_found"})
        return json.loads(row["receipt_json"])

    @app.get("/api/v1/ledgers/{ledger_id}/receipts")
    def ledger_receipts(ledger_id: str, request: Request) -> dict[str, Any]:
        if not settings.enable_ledger_query:
            raise HTTPException(status_code=404, detail={"code": "not_found"})
        _require_token(request, settings, settings.api_token, "api")
        if not ledger_id or len(ledger_id) > 128:
            raise HTTPException(status_code=422, detail={"code": "invalid_ledger_id"})
        rows = db.execute(
            "SELECT receipt_json FROM anchors WHERE ledger_id = ? ORDER BY server_sequence",
            (ledger_id,),
        ).fetchall()
        return {"format": "bac.anchor.ledger_receipts.v1", "ledger_id": ledger_id, "receipts": [json.loads(row["receipt_json"]) for row in rows]}

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request) -> str:
        if _is_production(settings) and not settings.admin_token:
            raise HTTPException(status_code=404, detail={"code": "not_found"})
        _require_token(request, settings, settings.admin_token, "admin")
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


def _find_existing_anchor(db: Any, anchor_hash: str, ledger_id: str, sequence: int) -> Any:
    return db.execute(
        """
        SELECT receipt_json FROM anchors
        WHERE anchor_hash = ? AND ledger_id = ? AND client_sequence = ?
        """,
        (anchor_hash, ledger_id, sequence),
    ).fetchone()


def _is_production(settings: Any) -> bool:
    return settings.env == "production"


def _require_token(request: Request, settings: Any, expected: str | None, label: str) -> None:
    if not _is_production(settings):
        return
    if not expected:
        raise HTTPException(status_code=503, detail={"code": f"{label}_token_not_configured"})
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail={"code": "authentication_required"})
    provided = authorization[len(prefix) :]
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _rate_limit_key(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if authorization:
        return authorization
    if request.client:
        return request.client.host
    return "unknown"


class _RateLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._lock = Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        window = int(now // 60)
        with self._lock:
            _started_at, count = self._buckets.get(key, (window, 0))
            if _started_at != window:
                self._buckets[key] = (window, 1)
                return True
            if count >= self.limit:
                return False
            self._buckets[key] = (window, count + 1)
            return True


class BodyLimitMiddleware:
    def __init__(self, app: Any, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            body.extend(chunk)
            if len(body) > self.max_body_bytes:
                response = JSONResponse({"detail": "request body is too large"}, status_code=413)
                await response(scope, receive, send)
                return
            more_body = message.get("more_body", False)

        sent = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)


app = create_app()
