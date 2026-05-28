"""Hash-chain helpers for BAC events."""

from __future__ import annotations

import hashlib
from typing import Any

from bac.core.canonicalize import canonical_bytes, strip_hash_fields


HASH_PREFIX = "sha256:"


def sha256_digest(data: bytes) -> str:
    return f"{HASH_PREFIX}{hashlib.sha256(data).hexdigest()}"


def hash_json(value: Any) -> str:
    return sha256_digest(canonical_bytes(value))


def compute_event_hash(event: dict[str, Any]) -> str:
    return hash_json(strip_hash_fields(event))


def attach_event_hash(event: dict[str, Any]) -> dict[str, Any]:
    event_with_hash = dict(event)
    event_with_hash["event_hash"] = compute_event_hash(event_with_hash)
    return event_with_hash


def is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(HASH_PREFIX):
        return False
    digest = value.removeprefix(HASH_PREFIX)
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
