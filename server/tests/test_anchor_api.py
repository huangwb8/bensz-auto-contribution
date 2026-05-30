from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

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


def _fresh_app(db_path: Path):
    os.environ["BAC_ANCHOR_ENV"] = "development"
    os.environ["BAC_ANCHOR_DB_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("BAC_ANCHOR_PRIVATE_KEY_B64", None)
    os.environ.pop("BAC_ANCHOR_PRIVATE_KEY_PATH", None)
    module = importlib.import_module("server.app.main")
    module = importlib.reload(module)
    return module.create_app()


if __name__ == "__main__":
    unittest.main()
