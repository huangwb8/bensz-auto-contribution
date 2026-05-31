"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS signing_keys (
  key_id TEXT PRIMARY KEY,
  public_key_b64 TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
  created_at TEXT NOT NULL,
  retired_at TEXT
);

CREATE TABLE IF NOT EXISTS anchors (
  receipt_id TEXT PRIMARY KEY,
  anchor_hash TEXT NOT NULL,
  ledger_id TEXT NOT NULL DEFAULT '',
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
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
"""


def sqlite_path(db_url: str) -> str:
    if db_url == "sqlite:///:memory:":
        return ":memory:"
    if db_url.startswith("sqlite:///"):
        return db_url.removeprefix("sqlite:///")
    raise ValueError("BAC_ANCHOR_DB_URL must start with sqlite:///")


def connect(db_url: str) -> sqlite3.Connection:
    path = sqlite_path(db_url)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    _migrate_anchors(connection)
    connection.commit()
    return connection


def _migrate_anchors(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        DELETE FROM anchors
        WHERE ledger_id IS NULL
          AND EXISTS (
            SELECT 1
            FROM anchors AS normalized
            WHERE normalized.anchor_hash = anchors.anchor_hash
              AND normalized.ledger_id = ''
              AND normalized.client_sequence = anchors.client_sequence
          )
        """
    )
    connection.execute(
        """
        DELETE FROM anchors
        WHERE ledger_id IS NULL
          AND rowid NOT IN (
            SELECT MIN(rowid)
            FROM anchors
            WHERE ledger_id IS NULL
            GROUP BY anchor_hash, client_sequence
          )
        """
    )
    connection.execute("UPDATE anchors SET ledger_id = '' WHERE ledger_id IS NULL")
