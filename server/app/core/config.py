"""Runtime configuration for the BAC anchor server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    env: str
    db_url: str
    private_key_b64: str | None
    private_key_path: str | None
    key_id: str | None


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("BAC_ANCHOR_ENV", "development"),
        db_url=os.getenv("BAC_ANCHOR_DB_URL", "sqlite:///./bac-anchor.sqlite3"),
        private_key_b64=os.getenv("BAC_ANCHOR_PRIVATE_KEY_B64"),
        private_key_path=os.getenv("BAC_ANCHOR_PRIVATE_KEY_PATH"),
        key_id=os.getenv("BAC_ANCHOR_KEY_ID"),
    )
