"""Canonical JSON serialization for BAC events."""

from __future__ import annotations

import json
from typing import Any


HASH_EXCLUDED_FIELDS = {"event_hash"}


def strip_hash_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Return an event copy without fields that would make hashes circular."""

    return {key: item for key, item in value.items() if key not in HASH_EXCLUDED_FIELDS}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")
