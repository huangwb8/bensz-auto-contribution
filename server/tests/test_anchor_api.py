from __future__ import annotations

import base64
import concurrent.futures
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from bac.core.anchor import verify_anchor_receipt


class AnchorApiTests(unittest.TestCase):
    def test_anchor_api_creates_idempotent_verifiable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(Path(tmp) / "anchor.sqlite3")
            client = TestClient(app)

            keys = client.get("/api/v1/public-keys")
            self.assertEqual(keys.status_code, 200)
            key_map = {item["key_id"]: item["public_key"] for item in keys.json()["keys"]}

            request = {
                "format": "bac.anchor.request.v1",
                "anchor_hash": "sha256:" + "6" * 64,
                "client_created_at": "2026-05-30T00:00:00Z",
                "ledger_public_key": None,
                "ledger_id": "ledger-1",
                "sequence": 1,
            }
            created = client.post("/api/v1/anchors", json=request)
            repeated = client.post("/api/v1/anchors", json=request)

            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            receipt = created.json()
            public_key = key_map[receipt["key_id"]]
            self.assertEqual(receipt, repeated.json())
            self.assertTrue(verify_anchor_receipt(receipt, public_key).valid)

            fetched = client.get(f"/api/v1/receipts/{receipt['receipt_id']}")
            self.assertEqual(fetched.json(), receipt)

    def test_anchor_api_rejects_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(Path(tmp) / "anchor.sqlite3")
            client = TestClient(app)

            response = client.post(
                "/api/v1/anchors",
                json={
                    "format": "bac.anchor.request.v1",
                    "anchor_hash": "sha256:" + "7" * 64,
                    "client_created_at": "2026-05-30T00:00:00Z",
                    "ledger_public_key": None,
                    "ledger_id": None,
                    "sequence": 1,
                    "path": "secret.py",
                },
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn("unsupported field", str(response.json()))

    def test_production_requires_api_token_for_anchor_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(
                Path(tmp) / "anchor.sqlite3",
                env="production",
                api_token="write-token",
                admin_token="admin-token",
            )
            client = TestClient(app)

            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/api/v1/public-keys").status_code, 200)

            request = _anchor_request()
            unauthenticated = client.post("/api/v1/anchors", json=request)
            wrong_token = client.post("/api/v1/anchors", json=request, headers={"Authorization": "Bearer wrong"})
            authenticated = client.post(
                "/api/v1/anchors",
                json=request,
                headers={"Authorization": "Bearer write-token"},
            )

            self.assertEqual(unauthenticated.status_code, 401)
            self.assertEqual(wrong_token.status_code, 403)
            self.assertEqual(authenticated.status_code, 200, authenticated.text)

    def test_cloud_register_login_create_ledger_and_anchor_with_user_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(
                Path(tmp) / "anchor.sqlite3",
                env="production",
                admin_token="admin-token",
            )
            client = TestClient(app)

            registered = client.post(
                "/api/v1/auth/register",
                json={"email": "User@Example.com", "password": "correct horse battery staple"},
            )
            self.assertEqual(registered.status_code, 200, registered.text)
            token = registered.json()["token"]

            logged_in = client.post(
                "/api/v1/auth/login",
                json={"email": "user@example.com", "password": "correct horse battery staple"},
            )
            self.assertEqual(logged_in.status_code, 200, logged_in.text)
            self.assertNotEqual(logged_in.json()["token"], token)

            ledger = client.post(
                "/api/v1/cloud/ledgers",
                json={"display_name": "demo project"},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(ledger.status_code, 200, ledger.text)
            ledger_id = ledger.json()["ledger_id"]

            request = _anchor_request(ledger_id=ledger_id)
            request["client_summary"] = {"event_count": 2, "source_counts": {"human": 1, "ai": 1}}
            anchored = client.post(
                "/api/v1/anchors",
                json=request,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(anchored.status_code, 200, anchored.text)

            me = client.get("/api/v1/cloud/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me.status_code, 200, me.text)
            self.assertEqual(me.json()["ledgers"][0]["ledger_id"], ledger_id)
            self.assertEqual(me.json()["ledgers"][0]["last_sequence"], 1)

    def test_user_token_cannot_anchor_to_another_users_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(
                Path(tmp) / "anchor.sqlite3",
                env="production",
                admin_token="admin-token",
            )
            client = TestClient(app)

            first = client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "password-123"})
            second = client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "password-456"})
            first_token = first.json()["token"]
            second_token = second.json()["token"]
            ledger = client.post(
                "/api/v1/cloud/ledgers",
                json={"display_name": "private"},
                headers={"Authorization": f"Bearer {first_token}"},
            )

            response = client.post(
                "/api/v1/anchors",
                json=_anchor_request(ledger_id=ledger.json()["ledger_id"]),
                headers={"Authorization": f"Bearer {second_token}"},
            )

            self.assertEqual(response.status_code, 403)

    def test_production_restricts_admin_to_admin_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(
                Path(tmp) / "anchor.sqlite3",
                env="production",
                api_token="write-token",
                admin_token="admin-token",
            )
            client = TestClient(app)

            self.assertEqual(client.get("/admin").status_code, 401)
            self.assertEqual(client.get("/admin", headers={"Authorization": "Bearer write-token"}).status_code, 403)
            self.assertEqual(client.get("/admin", headers={"Authorization": "Bearer admin-token"}).status_code, 200)

    def test_production_can_disable_or_restrict_ledger_receipt_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disabled = _fresh_app(
                Path(tmp) / "disabled.sqlite3",
                env="production",
                api_token="write-token",
                admin_token="admin-token",
                enable_ledger_query=False,
            )
            self.assertEqual(TestClient(disabled).get("/api/v1/ledgers/ledger-1/receipts").status_code, 404)

            enabled = _fresh_app(
                Path(tmp) / "enabled.sqlite3",
                env="production",
                api_token="write-token",
                admin_token="admin-token",
                enable_ledger_query=True,
            )
            client = TestClient(enabled)

            self.assertEqual(client.get("/api/v1/ledgers/ledger-1/receipts").status_code, 401)
            self.assertEqual(
                client.get("/api/v1/ledgers/ledger-1/receipts", headers={"Authorization": "Bearer wrong"}).status_code,
                403,
            )
            authorized = client.get(
                "/api/v1/ledgers/ledger-1/receipts",
                headers={"Authorization": "Bearer write-token"},
            )
            self.assertEqual(authorized.status_code, 200, authorized.text)

    def test_request_body_limit_counts_actual_bytes_without_content_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(Path(tmp) / "anchor.sqlite3", max_body_bytes=64)
            response = _asgi_request_without_content_length(
                app,
                "/api/v1/anchors",
                json.dumps(_anchor_request()).encode("utf-8"),
            )

            self.assertEqual(response["status"], 413)

    def test_production_rate_limits_repeated_anchor_requests_from_same_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(
                Path(tmp) / "anchor.sqlite3",
                env="production",
                api_token="write-token",
                admin_token="admin-token",
                rate_limit_per_minute=2,
            )
            client = TestClient(app)
            headers = {"Authorization": "Bearer write-token"}

            first = client.post("/api/v1/anchors", json=_anchor_request(), headers=headers)
            second = client.post("/api/v1/anchors", json=_anchor_request(), headers=headers)
            third = client.post("/api/v1/anchors", json=_anchor_request(), headers=headers)

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(third.status_code, 429, third.text)
            self.assertEqual(client.get("/healthz").status_code, 200)

    def test_null_ledger_id_anchor_requests_are_idempotent_under_parallel_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _fresh_app(Path(tmp) / "anchor.sqlite3")
            request = _anchor_request(ledger_id=None)

            def post_anchor():
                with TestClient(app) as client:
                    return client.post("/api/v1/anchors", json=request)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                responses = list(executor.map(lambda _index: post_anchor(), range(8)))

            self.assertTrue(all(response.status_code == 200 for response in responses))
            receipts = [response.json() for response in responses]
            self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
            count = app.state.db.execute("SELECT COUNT(*) AS count FROM anchors").fetchone()["count"]
            self.assertEqual(count, 1)

    def test_existing_null_ledger_rows_are_migrated_for_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite3"
            _create_legacy_anchor_db(db_path)
            app = _fresh_app(db_path)
            client = TestClient(app)

            response = client.post("/api/v1/anchors", json=_anchor_request(ledger_id=None))

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["receipt_id"], "legacy-receipt")
            count = app.state.db.execute("SELECT COUNT(*) AS count FROM anchors").fetchone()["count"]
            null_count = app.state.db.execute("SELECT COUNT(*) AS count FROM anchors WHERE ledger_id IS NULL").fetchone()["count"]
            self.assertEqual(count, 2)
            self.assertEqual(null_count, 0)


def _fresh_app(
    db_path: Path,
    *,
    env: str = "development",
    api_token: str | None = None,
    admin_token: str | None = None,
    enable_ledger_query: bool | None = None,
    max_body_bytes: int | None = None,
    rate_limit_per_minute: int | None = None,
):
    os.environ["BAC_ANCHOR_ENV"] = env
    os.environ["BAC_ANCHOR_DB_URL"] = f"sqlite:///{db_path}"
    _set_or_pop("BAC_ANCHOR_API_TOKEN", api_token)
    _set_or_pop("BAC_ANCHOR_ADMIN_TOKEN", admin_token)
    _set_or_pop(
        "BAC_ANCHOR_ENABLE_LEDGER_QUERY",
        None if enable_ledger_query is None else ("true" if enable_ledger_query else "false"),
    )
    _set_or_pop("BAC_ANCHOR_MAX_BODY_BYTES", None if max_body_bytes is None else str(max_body_bytes))
    _set_or_pop(
        "BAC_ANCHOR_RATE_LIMIT_PER_MINUTE",
        None if rate_limit_per_minute is None else str(rate_limit_per_minute),
    )
    if env == "production":
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        os.environ["BAC_ANCHOR_PRIVATE_KEY_B64"] = base64.b64encode(private_bytes).decode("ascii")
    else:
        os.environ.pop("BAC_ANCHOR_PRIVATE_KEY_B64", None)
    os.environ.pop("BAC_ANCHOR_PRIVATE_KEY_PATH", None)
    module = importlib.import_module("server.app.main")
    module = importlib.reload(module)
    return module.create_app()


def _set_or_pop(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _anchor_request(ledger_id: str | None = "ledger-1") -> dict:
    return {
        "format": "bac.anchor.request.v1",
        "anchor_hash": "sha256:" + "6" * 64,
        "client_created_at": "2026-05-30T00:00:00Z",
        "ledger_public_key": None,
        "ledger_id": ledger_id,
        "sequence": 1,
    }


def _asgi_request_without_content_length(app, path: str, body: bytes) -> dict:
    import anyio

    async def run_request() -> dict:
        messages = []
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        start = next(message for message in messages if message["type"] == "http.response.start")
        return {"status": start["status"], "messages": messages}

    return anyio.run(run_request)


def _create_legacy_anchor_db(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE anchors (
          receipt_id TEXT PRIMARY KEY,
          anchor_hash TEXT NOT NULL,
          ledger_id TEXT,
          client_sequence INTEGER NOT NULL,
          server_sequence INTEGER NOT NULL,
          client_created_at TEXT NOT NULL,
          server_created_at TEXT NOT NULL,
          key_id TEXT NOT NULL,
          signature_alg TEXT NOT NULL,
          signature_b64 TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          receipt_json TEXT NOT NULL,
          UNIQUE(anchor_hash, ledger_id, client_sequence)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO anchors (
          receipt_id, anchor_hash, ledger_id, client_sequence, server_sequence,
          client_created_at, server_created_at, key_id, signature_alg,
          signature_b64, request_hash, receipt_json
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-receipt",
            "sha256:" + "6" * 64,
            1,
            1,
            "2026-05-30T00:00:00Z",
            "2026-05-30T00:00:01Z",
            "legacy-key",
            "Ed25519",
            "signature",
            "request-hash",
            '{"receipt_id":"legacy-receipt"}',
        ),
    )
    connection.execute(
        """
        INSERT INTO anchors (
          receipt_id, anchor_hash, ledger_id, client_sequence, server_sequence,
          client_created_at, server_created_at, key_id, signature_alg,
          signature_b64, request_hash, receipt_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "normalized-receipt",
            "sha256:" + "7" * 64,
            "",
            1,
            2,
            "2026-05-30T00:00:00Z",
            "2026-05-30T00:00:02Z",
            "legacy-key",
            "Ed25519",
            "signature",
            "request-hash",
            '{"receipt_id":"normalized-receipt"}',
        ),
    )
    connection.execute(
        """
        INSERT INTO anchors (
          receipt_id, anchor_hash, ledger_id, client_sequence, server_sequence,
          client_created_at, server_created_at, key_id, signature_alg,
          signature_b64, request_hash, receipt_json
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "duplicate-null-receipt",
            "sha256:" + "7" * 64,
            1,
            3,
            "2026-05-30T00:00:00Z",
            "2026-05-30T00:00:03Z",
            "legacy-key",
            "Ed25519",
            "signature",
            "request-hash",
            '{"receipt_id":"duplicate-null-receipt"}',
        ),
    )
    connection.execute(
        """
        CREATE TABLE signing_keys (
          key_id TEXT PRIMARY KEY,
          public_key_b64 TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
          created_at TEXT NOT NULL,
          retired_at TEXT
        )
        """
    )
    connection.commit()
    connection.close()


if __name__ == "__main__":
    unittest.main()
