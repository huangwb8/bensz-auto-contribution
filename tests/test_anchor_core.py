from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from bac.core.anchor import (
    build_anchor_request,
    compute_anchor_hash,
    signing_payload_for_receipt,
    validate_anchor_request,
    verify_anchor_receipt,
)
from bac.core.canonicalize import canonical_bytes
from bac.core.verify import verify_bac_file
from bac.service.event_builder import build_anchor_checkpoint_event, build_genesis_event, build_record_event
from bac.storage.bac_file import append_event, initialize_bac_file


class AnchorCoreTests(unittest.TestCase):
    def test_compute_anchor_hash_is_stable_blinded_and_input_sensitive(self) -> None:
        head_hash = "sha256:" + "1" * 64
        nonce = "ledger-nonce"

        first = compute_anchor_hash(head_hash, nonce)
        second = compute_anchor_hash(head_hash, nonce)

        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(first, head_hash)
        self.assertNotEqual(first, compute_anchor_hash("sha256:" + "2" * 64, nonce))
        self.assertNotEqual(first, compute_anchor_hash(head_hash, "other-nonce"))

    def test_anchor_request_validation_rejects_private_fields(self) -> None:
        request = build_anchor_request(
            head_hash="sha256:" + "3" * 64,
            ledger_nonce="nonce",
            sequence=1,
            ledger_id="ledger-1",
        )
        self.assertFalse(validate_anchor_request(request))
        request["path"] = "src/private.py"
        request["head_hash"] = "sha256:" + "3" * 64

        errors = validate_anchor_request(request)

        self.assertTrue(any("unsupported field" in error for error in errors))

    def test_receipt_signature_verification_detects_tampering(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key_b64 = base64.b64encode(public_key).decode("ascii")
        receipt = {
            "format": "bac.anchor.receipt.v1",
            "anchor_hash": "sha256:" + "4" * 64,
            "server_created_at": "2026-05-30T00:00:03Z",
            "service": "bac-anchor",
            "key_id": "test-key",
            "signature_alg": "Ed25519",
            "receipt_id": "bac_receipt_test",
            "sequence": 1,
            "server_sequence": 7,
        }
        receipt["signature"] = base64.b64encode(
            private_key.sign(canonical_bytes(signing_payload_for_receipt(receipt)))
        ).decode("ascii")

        self.assertTrue(verify_anchor_receipt(receipt, public_key_b64).valid)

        tampered = dict(receipt)
        tampered["anchor_hash"] = "sha256:" + "5" * 64
        self.assertFalse(verify_anchor_receipt(tampered, public_key_b64).valid)

        wrong_public_key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.assertFalse(verify_anchor_receipt(receipt, base64.b64encode(wrong_public_key).decode("ascii")).valid)

    def test_verify_anchored_checkpoint_binds_receipt_to_previous_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_key = Ed25519PrivateKey.generate()
            public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            public_key_b64 = base64.b64encode(public_key).decode("ascii")
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            record = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation",
            )
            append_event(bac_file, record)
            nonce = "local-ledger-nonce"
            receipt = _signed_receipt(private_key, compute_anchor_hash(record["event_hash"], nonce))
            checkpoint = build_anchor_checkpoint_event(
                root=root,
                prev_event_hash=record["event_hash"],
                anchor_receipt=receipt,
                ledger_nonce=nonce,
                anchor_public_key=public_key_b64,
                summary="Remote anchor checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "pass", report.errors)
            self.assertEqual(report.anchor_status, "receipt_valid")

    def test_cli_anchor_request_import_and_require_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            _run_cli(root, env, "init", "--mode", "hybrid", "--json")
            record = _run_cli(
                root,
                env,
                "record",
                "--event-type",
                "ai_generation",
                "--source-type",
                "ai",
                "--summary",
                "Generated anchor workflow",
                "--json",
            )
            head_hash = json.loads(record.stdout)["head_hash"]

            request = _run_cli(root, env, "anchor", "request", "--json")
            request_payload = json.loads(request.stdout)
            self.assertNotIn("head_hash", request_payload)

            private_key = Ed25519PrivateKey.generate()
            public_key_b64 = base64.b64encode(
                private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            ).decode("ascii")
            receipt = _signed_receipt(private_key, request_payload["anchor_hash"])
            receipt_file = root / "receipt.json"
            receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

            _run_cli(
                root,
                env,
                "anchor",
                "import",
                "--receipt-file",
                str(receipt_file),
                "--public-key",
                public_key_b64,
                "--json",
            )
            verify = _run_cli(root, env, "verify", "--require-anchor", "--json")
            report = json.loads(verify.stdout)

            self.assertEqual(report["status"], "pass", report["errors"])
            self.assertEqual(report["anchor_status"], "receipt_valid")
            self.assertEqual(head_hash, report["anchored_head_hashes"][-1])


def _signed_receipt(private_key: Ed25519PrivateKey, anchor_hash: str) -> dict:
    receipt = {
        "format": "bac.anchor.receipt.v1",
        "anchor_hash": anchor_hash,
        "server_created_at": "2026-05-30T00:00:03Z",
        "service": "bac-anchor",
        "key_id": "test-key",
        "signature_alg": "Ed25519",
        "receipt_id": "bac_receipt_test",
        "sequence": 1,
        "server_sequence": 1,
    }
    receipt["signature"] = base64.b64encode(
        private_key.sign(canonical_bytes(signing_payload_for_receipt(receipt)))
    ).decode("ascii")
    return receipt


def _run_cli(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "bac", "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result
