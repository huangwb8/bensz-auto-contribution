"""Runtime configuration for the BAC anchor server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    env: str
    db_url: str
    redis_url: str | None
    private_key_b64: str | None
    private_key_path: str | None
    key_id: str | None
    api_token: str | None
    admin_token: str | None
    enable_ledger_query: bool
    max_body_bytes: int
    rate_limit_per_minute: int


def load_settings() -> Settings:
    env = os.getenv("BAC_ANCHOR_ENV", "development")
    return Settings(
        env=env,
        db_url=os.getenv("BAC_ANCHOR_DB_URL", "sqlite:///./bac-anchor.sqlite3"),
        redis_url=os.getenv("BAC_ANCHOR_REDIS_URL"),
        private_key_b64=os.getenv("BAC_ANCHOR_PRIVATE_KEY_B64"),
        private_key_path=os.getenv("BAC_ANCHOR_PRIVATE_KEY_PATH"),
        key_id=os.getenv("BAC_ANCHOR_KEY_ID"),
        api_token=os.getenv("BAC_ANCHOR_API_TOKEN"),
        admin_token=os.getenv("BAC_ANCHOR_ADMIN_TOKEN"),
        enable_ledger_query=_bool_env("BAC_ANCHOR_ENABLE_LEDGER_QUERY", default=env != "production"),
        max_body_bytes=_int_env("BAC_ANCHOR_MAX_BODY_BYTES", default=8192),
        rate_limit_per_minute=_int_env("BAC_ANCHOR_RATE_LIMIT_PER_MINUTE", default=120),
    )


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, 0)
