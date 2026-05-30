"""Private anchor request, receipt, and signature helpers."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bac.core.canonicalize import canonical_bytes
from bac.core.hash_chain import hash_json, is_sha256


ANCHOR_REQUEST_FORMAT = "bac.anchor.request.v1"
ANCHOR_RECEIPT_FORMAT = "bac.anchor.receipt.v1"
ANCHOR_SIGNING_PAYLOAD_FORMAT = "bac.anchor.receipt.signing_payload.v1"
ANCHOR_DOMAIN = "bac.anchor.v1"
SERVICE_NAME = "bac-anchor"

REQUEST_FIELDS = {
    "format",
    "anchor_hash",
    "client_created_at",
    "ledger_public_key",
    "ledger_id",
    "sequence",
}
FORBIDDEN_REQUEST_FIELDS = {
    "actor",
    "diff",
    "head_hash",
    "path",
    "payload",
    "project",
    "prompt",
    "repo",
    "repository",
}
RECEIPT_FIELDS = {
    "format",
    "anchor_hash",
    "server_created_at",
    "service",
    "key_id",
    "signature_alg",
    "receipt_id",
    "sequence",
    "signature",
}
OPTIONAL_RECEIPT_FIELDS = {"server_sequence"}


@dataclass
class ReceiptVerification:
    valid: bool
    errors: list[str] = field(default_factory=list)


def compute_anchor_hash(head_hash: str, ledger_nonce: str) -> str:
    if not is_sha256(head_hash):
        raise ValueError("head_hash must be sha256:<64 lowercase hex chars>")
    if not isinstance(ledger_nonce, str) or not ledger_nonce:
        raise ValueError("ledger_nonce must be a non-empty string")
    return hash_json(
        {
            "domain": ANCHOR_DOMAIN,
            "head_hash": head_hash,
            "ledger_nonce": ledger_nonce,
        }
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_anchor_request(
    *,
    head_hash: str,
    ledger_nonce: str,
    sequence: int,
    ledger_id: str | None = None,
    ledger_public_key: str | None = None,
    client_created_at: str | None = None,
) -> dict[str, Any]:
    request = {
        "format": ANCHOR_REQUEST_FORMAT,
        "anchor_hash": compute_anchor_hash(head_hash, ledger_nonce),
        "client_created_at": client_created_at or utc_now(),
        "ledger_public_key": ledger_public_key,
        "ledger_id": ledger_id,
        "sequence": sequence,
    }
    errors = validate_anchor_request(request)
    if errors:
        raise ValueError("; ".join(errors))
    return request


def validate_anchor_request(request: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(request, dict):
        return ["anchor request must be an object"]

    for key in sorted(set(request) - REQUEST_FIELDS):
        errors.append(f"unsupported field in anchor request: {key}")
    for key in sorted(FORBIDDEN_REQUEST_FIELDS & set(request)):
        errors.append(f"private field is not allowed in anchor request: {key}")

    if request.get("format") != ANCHOR_REQUEST_FORMAT:
        errors.append(f"format must be {ANCHOR_REQUEST_FORMAT}")
    if not is_sha256(request.get("anchor_hash")):
        errors.append("anchor_hash must be sha256:<64 lowercase hex chars>")
    _validate_utc_timestamp(request.get("client_created_at"), "client_created_at", errors)
    _validate_optional_identifier(request.get("ledger_id"), "ledger_id", 128, errors)
    _validate_optional_public_key(request.get("ledger_public_key"), "ledger_public_key", errors)
    _validate_sequence(request.get("sequence"), "sequence", errors)
    return errors


def validate_anchor_receipt(receipt: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["anchor receipt must be an object"]

    missing = sorted(RECEIPT_FIELDS - set(receipt))
    if missing:
        errors.append(f"missing required receipt fields: {', '.join(missing)}")
    for key in sorted(set(receipt) - RECEIPT_FIELDS - OPTIONAL_RECEIPT_FIELDS):
        errors.append(f"unsupported field in anchor receipt: {key}")

    if receipt.get("format") != ANCHOR_RECEIPT_FORMAT:
        errors.append(f"format must be {ANCHOR_RECEIPT_FORMAT}")
    if not is_sha256(receipt.get("anchor_hash")):
        errors.append("anchor_hash must be sha256:<64 lowercase hex chars>")
    _validate_utc_timestamp(receipt.get("server_created_at"), "server_created_at", errors)
    if receipt.get("service") != SERVICE_NAME:
        errors.append(f"service must be {SERVICE_NAME}")
    if receipt.get("signature_alg") != "Ed25519":
        errors.append("signature_alg must be Ed25519")
    _validate_required_identifier(receipt.get("key_id"), "key_id", 128, errors)
    _validate_required_identifier(receipt.get("receipt_id"), "receipt_id", 160, errors)
    _validate_sequence(receipt.get("sequence"), "sequence", errors)
    if "server_sequence" in receipt:
        _validate_sequence(receipt.get("server_sequence"), "server_sequence", errors)
    _decode_base64(receipt.get("signature"), "signature", errors)
    return errors


def signing_payload_for_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "format": ANCHOR_SIGNING_PAYLOAD_FORMAT,
        "anchor_hash": receipt.get("anchor_hash"),
        "server_created_at": receipt.get("server_created_at"),
        "service": receipt.get("service"),
        "key_id": receipt.get("key_id"),
        "signature_alg": receipt.get("signature_alg"),
        "receipt_id": receipt.get("receipt_id"),
        "sequence": receipt.get("sequence"),
    }
    if "server_sequence" in receipt:
        payload["server_sequence"] = receipt.get("server_sequence")
    return payload


def verify_anchor_receipt(receipt: dict[str, Any], public_key: str) -> ReceiptVerification:
    errors = validate_anchor_receipt(receipt)
    public_key_bytes = _decode_base64(public_key, "public_key", errors)
    signature_bytes = _decode_base64(receipt.get("signature"), "signature", errors)
    if public_key_bytes is not None and len(public_key_bytes) != 32:
        errors.append("public_key must be a base64-encoded raw Ed25519 public key")
    if signature_bytes is not None and len(signature_bytes) != 64:
        errors.append("signature must be a base64-encoded Ed25519 signature")
    if errors:
        return ReceiptVerification(valid=False, errors=errors)

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        return ReceiptVerification(
            valid=False,
            errors=["cryptography is required for Ed25519 receipt verification; install bensz-auto-contribution[anchor]"],
        )

    try:
        verifier = Ed25519PublicKey.from_public_bytes(public_key_bytes or b"")
        verifier.verify(signature_bytes or b"", canonical_bytes(signing_payload_for_receipt(receipt)))
    except InvalidSignature:
        return ReceiptVerification(valid=False, errors=["receipt signature is invalid"])
    except ValueError as exc:
        return ReceiptVerification(valid=False, errors=[f"public_key is invalid: {exc}"])
    return ReceiptVerification(valid=True)


def _validate_utc_timestamp(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        errors.append(f"{label} must be an ISO-8601 UTC timestamp ending with Z")
        return
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        errors.append(f"{label} must be an ISO-8601 UTC timestamp ending with Z")


def _validate_required_identifier(value: Any, label: str, max_length: int, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty string")
        return
    _validate_optional_identifier(value, label, max_length, errors)


def _validate_optional_identifier(value: Any, label: str, max_length: int, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be null or a non-empty string")
        return
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if len(value) > max_length or any(char not in allowed for char in value):
        errors.append(f"{label} contains unsupported characters or is too long")


def _validate_optional_public_key(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    decoded = _decode_base64(value, label, errors)
    if decoded is not None and len(decoded) != 32:
        errors.append(f"{label} must be a base64-encoded raw Ed25519 public key")


def _validate_sequence(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 2**63 - 1:
        errors.append(f"{label} must be an integer between 0 and 2^63-1")


def _decode_base64(value: Any, label: str, errors: list[str]) -> bytes | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty base64 string")
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        errors.append(f"{label} must be valid base64")
        return None
