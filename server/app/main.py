"""FastAPI reference implementation for BAC private anchors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
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
from server.app.db.session import DatabaseIntegrityError, connect
from server.app.signing.keys import load_signing_key

try:
    import redis
except ImportError:  # pragma: no cover - exercised when server extras are not installed.
    redis = None


PASSWORD_ITERATIONS = 210_000


def create_app() -> FastAPI:
    settings = load_settings()
    signing_key = load_signing_key(settings)
    db = connect(settings.db_url)
    lock = Lock()
    rate_limiter = build_rate_limiter(settings)
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

    @app.get("/cloud", response_class=HTMLResponse)
    def cloud_console() -> str:
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BAC Cloud</title>
<style>
body{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.5}
label{display:block;margin-top:12px}
input{box-sizing:border-box;width:100%;padding:8px;margin-top:4px}
button{margin-top:14px;padding:8px 12px}
pre{background:#f6f8fa;padding:12px;overflow:auto}
</style>
</head>
<body>
<h1>BAC Cloud</h1>
<p>Register or log in to create a local CLI token. Store the token outside your .bac file.</p>
<label>Email<input id="email" type="email" autocomplete="username"></label>
<label>Password<input id="password" type="password" autocomplete="current-password"></label>
<button onclick="submitAuth('/api/v1/auth/register')">Register</button>
<button onclick="submitAuth('/api/v1/auth/login')">Log in</button>
<pre id="output"></pre>
<script>
async function submitAuth(path) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      email: document.getElementById('email').value,
      password: document.getElementById('password').value
    })
  });
  document.getElementById('output').textContent = JSON.stringify(await response.json(), null, 2);
}
</script>
</body>
</html>"""

    @app.post("/api/v1/auth/register")
    async def register(request: Request) -> dict[str, Any]:
        payload = await request.json()
        email = _normalize_email(payload.get("email"))
        password = payload.get("password")
        if not email:
            raise HTTPException(status_code=422, detail={"code": "invalid_email"})
        if not isinstance(password, str) or len(password) < 8:
            raise HTTPException(status_code=422, detail={"code": "weak_password"})
        if db.execute("SELECT user_id FROM users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(status_code=409, detail={"code": "email_already_registered"})

        user_id = f"user_{uuid.uuid4().hex}"
        salt_b64, password_hash_b64 = _hash_password(password)
        created_at = utc_now()
        db.execute(
            """
            INSERT INTO users (
              user_id, email, password_salt_b64, password_hash_b64,
              password_iterations, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, salt_b64, password_hash_b64, PASSWORD_ITERATIONS, created_at),
        )
        token = _issue_user_token(db, user_id, "default")
        db.commit()
        return _auth_response(user_id, email, token)

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> dict[str, Any]:
        payload = await request.json()
        email = _normalize_email(payload.get("email"))
        password = payload.get("password")
        if not email or not isinstance(password, str):
            raise HTTPException(status_code=401, detail={"code": "invalid_credentials"})
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not _verify_password(password, row):
            raise HTTPException(status_code=401, detail={"code": "invalid_credentials"})
        token = _issue_user_token(db, row["user_id"], "cli-login")
        db.commit()
        return _auth_response(row["user_id"], row["email"], token)

    @app.get("/api/v1/cloud/me")
    def cloud_me(request: Request) -> dict[str, Any]:
        auth = _require_user_token(request, db, settings)
        ledgers = db.execute(
            """
            SELECT ledger_id, display_name, created_at, last_anchor_hash, last_sequence, last_seen_at
            FROM cloud_ledgers
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (auth["user_id"],),
        ).fetchall()
        return {
            "format": "bac.cloud.me.v1",
            "user": {"user_id": auth["user_id"], "email": auth["email"]},
            "ledgers": [dict(row) for row in ledgers],
        }

    @app.post("/api/v1/cloud/ledgers")
    async def create_cloud_ledger(request: Request) -> dict[str, Any]:
        auth = _require_user_token(request, db, settings)
        payload = await request.json()
        display_name = payload.get("display_name") if isinstance(payload, dict) else None
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = "BAC ledger"
        if len(display_name) > 120:
            raise HTTPException(status_code=422, detail={"code": "invalid_display_name"})
        ledger_id = f"ledger_{uuid.uuid4().hex}"
        created_at = utc_now()
        db.execute(
            """
            INSERT INTO cloud_ledgers (ledger_id, user_id, display_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (ledger_id, auth["user_id"], display_name.strip(), created_at),
        )
        db.commit()
        return {
            "format": "bac.cloud.ledger.v1",
            "ledger_id": ledger_id,
            "display_name": display_name.strip(),
            "created_at": created_at,
        }

    @app.post("/api/v1/anchors")
    async def create_anchor(request: Request) -> dict[str, Any]:
        auth = _optional_api_auth(request, db, settings)
        if _is_production(settings) and not auth:
            raise HTTPException(status_code=401, detail={"code": "authentication_required"})
        if _is_production(settings) and not rate_limiter.allow(_rate_limit_key(request)):
            raise HTTPException(status_code=429, detail={"code": "rate_limited"})
        payload = await request.json()
        errors = validate_anchor_request(payload)
        if errors:
            raise HTTPException(status_code=422, detail={"code": "invalid_anchor_request", "errors": errors})
        ledger_id = payload.get("ledger_id") or ""
        if auth and auth["kind"] == "user" and ledger_id:
            _require_ledger_owner(db, auth["user_id"], ledger_id)
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
                      signature_b64, request_hash, client_summary_json, receipt_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        canonical_json(payload.get("client_summary") or {}),
                        canonical_json(receipt),
                    ),
                )
                _mark_ledger_anchor(db, ledger_id, payload["anchor_hash"], payload["sequence"])
                db.commit()
            except DatabaseIntegrityError:
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
        auth = _optional_api_auth(request, db, settings)
        if _is_production(settings) and not auth:
            raise HTTPException(status_code=401, detail={"code": "authentication_required"})
        if not ledger_id or len(ledger_id) > 128:
            raise HTTPException(status_code=422, detail={"code": "invalid_ledger_id"})
        if auth and auth["kind"] == "user":
            _require_ledger_owner(db, auth["user_id"], ledger_id)
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


def _optional_api_auth(request: Request, db: Any, settings: Any) -> dict[str, Any] | None:
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    provided = authorization[len(prefix) :]
    if settings.api_token and secrets.compare_digest(provided, settings.api_token):
        return {"kind": "service"}
    row = db.execute(
        """
        SELECT api_tokens.token_id, api_tokens.user_id, users.email
        FROM api_tokens
        JOIN users ON users.user_id = api_tokens.user_id
        WHERE api_tokens.token_hash = ? AND api_tokens.revoked_at IS NULL
        """,
        (_token_hash(provided),),
    ).fetchone()
    if not row:
        if _is_production(settings):
            raise HTTPException(status_code=403, detail={"code": "forbidden"})
        return None
    db.execute("UPDATE api_tokens SET last_used_at = ? WHERE token_id = ?", (utc_now(), row["token_id"]))
    db.commit()
    return {"kind": "user", "user_id": row["user_id"], "email": row["email"]}


def _require_user_token(request: Request, db: Any, settings: Any) -> dict[str, Any]:
    auth = _optional_api_auth(request, db, settings)
    if not auth or auth["kind"] != "user":
        raise HTTPException(status_code=401, detail={"code": "user_token_required"})
    return auth


def _require_ledger_owner(db: Any, user_id: str, ledger_id: str) -> None:
    row = db.execute(
        "SELECT ledger_id FROM cloud_ledgers WHERE ledger_id = ? AND user_id = ?",
        (ledger_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail={"code": "ledger_forbidden"})


def _mark_ledger_anchor(db: Any, ledger_id: str, anchor_hash: str, sequence: int) -> None:
    if not ledger_id:
        return
    db.execute(
        """
        UPDATE cloud_ledgers
        SET last_anchor_hash = ?, last_sequence = ?, last_seen_at = ?
        WHERE ledger_id = ?
        """,
        (anchor_hash, sequence, utc_now(), ledger_id),
    )


def _normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if "@" not in email or len(email) > 254:
        return None
    return email


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PASSWORD_ITERATIONS)
    return base64.b64encode(salt_bytes).decode("ascii"), base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, row: Any) -> bool:
    try:
        salt = base64.b64decode(row["password_salt_b64"], validate=True)
        expected = base64.b64decode(row["password_hash_b64"], validate=True)
    except (binascii.Error, ValueError):
        return False
    iterations = int(row["password_iterations"])
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _issue_user_token(db: Any, user_id: str, label: str) -> str:
    token = "bac_" + secrets.token_urlsafe(32)
    db.execute(
        """
        INSERT INTO api_tokens (token_id, user_id, token_hash, label, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (f"token_{uuid.uuid4().hex}", user_id, _token_hash(token), label, utc_now()),
    )
    return token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _auth_response(user_id: str, email: str, token: str) -> dict[str, Any]:
    return {
        "format": "bac.cloud.auth.v1",
        "user": {"user_id": user_id, "email": email},
        "token": token,
        "token_type": "bearer",
    }


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


class _RedisRateLimiter:
    def __init__(self, redis_url: str, limit_per_minute: int) -> None:
        if redis is None:
            raise RuntimeError("Redis rate limiting requires installing the server extra dependencies")
        self.limit = limit_per_minute
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.client.ping()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        window = int(time.monotonic() // 60)
        redis_key = f"bac-anchor:rate-limit:{window}:{hash_json({'key': key})}"
        count = int(self.client.incr(redis_key))
        if count == 1:
            self.client.expire(redis_key, 120)
        return count <= self.limit


def build_rate_limiter(settings: Any) -> Any:
    if settings.redis_url:
        return _RedisRateLimiter(settings.redis_url, settings.rate_limit_per_minute)
    return _RateLimiter(settings.rate_limit_per_minute)


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
