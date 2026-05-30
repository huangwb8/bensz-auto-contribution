"""Ed25519 key loading for the anchor server."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from server.app.core.config import Settings


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    private_key: Ed25519PrivateKey
    public_key_b64: str


def load_signing_key(settings: Settings) -> SigningKey:
    private_key_bytes = _load_private_key_bytes(settings)
    if private_key_bytes is None:
        if settings.env == "production":
            raise RuntimeError("production mode requires BAC_ANCHOR_PRIVATE_KEY_PATH or BAC_ANCHOR_PRIVATE_KEY_B64")
        private_key = Ed25519PrivateKey.generate()
    else:
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key_b64 = base64.b64encode(public_key).decode("ascii")
    key_id = settings.key_id or "bac-anchor-ed25519-" + sha256(public_key).hexdigest()[:12]
    return SigningKey(key_id=key_id, private_key=private_key, public_key_b64=public_key_b64)


def _load_private_key_bytes(settings: Settings) -> bytes | None:
    raw = settings.private_key_b64
    if settings.private_key_path:
        raw = Path(settings.private_key_path).read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        private_key_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("anchor private key must be base64-encoded raw Ed25519 private key bytes") from exc
    if len(private_key_bytes) != 32:
        raise RuntimeError("anchor private key must decode to 32 raw Ed25519 private key bytes")
    return private_key_bytes
