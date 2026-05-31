"""Database connection and schema management for the anchor server."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SQLITE_SCHEMA = """
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
  client_summary_json TEXT,
  receipt_json TEXT NOT NULL,
  UNIQUE(anchor_hash, ledger_id, client_sequence)
);

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_salt_b64 TEXT NOT NULL,
  password_hash_b64 TEXT NOT NULL,
  password_iterations INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
  token_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS cloud_ledgers (
  ledger_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_anchor_hash TEXT,
  last_sequence INTEGER,
  last_seen_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
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
  client_summary_json TEXT,
  receipt_json TEXT NOT NULL,
  UNIQUE(anchor_hash, ledger_id, client_sequence)
);

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_salt_b64 TEXT NOT NULL,
  password_hash_b64 TEXT NOT NULL,
  password_iterations INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
  token_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  token_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS cloud_ledgers (
  ledger_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_anchor_hash TEXT,
  last_sequence BIGINT,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
"""


try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised when server extras are not installed.
    psycopg = None
    dict_row = None

DatabaseIntegrityError = (sqlite3.IntegrityError,)
if psycopg is not None:
    DatabaseIntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)


def sqlite_path(db_url: str) -> str:
    if db_url == "sqlite:///:memory:":
        return ":memory:"
    if db_url.startswith("sqlite:///"):
        return db_url.removeprefix("sqlite:///")
    raise ValueError("BAC_ANCHOR_DB_URL must start with sqlite:///")


def connect(db_url: str) -> Any:
    if db_url.startswith(("postgresql://", "postgres://")):
        return _connect_postgres(db_url)
    return _connect_sqlite(db_url)


def _connect_sqlite(db_url: str) -> sqlite3.Connection:
    path = sqlite_path(db_url)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SQLITE_SCHEMA)
    _migrate_sqlite_anchors(connection)
    _migrate_sqlite_cloud(connection)
    connection.commit()
    return connection


def _connect_postgres(db_url: str) -> Any:
    if psycopg is None:
        raise RuntimeError("PostgreSQL support requires installing the server extra dependencies")
    raw_connection = psycopg.connect(db_url, row_factory=dict_row)
    connection = PostgresConnection(raw_connection)
    for statement in _split_sql_statements(POSTGRES_SCHEMA):
        connection.execute(statement)
    _migrate_postgres_anchors(connection)
    _migrate_postgres_cloud(connection)
    connection.commit()
    return connection


class PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
        return self._connection.execute(sql.replace("?", "%s"), params)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def _split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def _migrate_sqlite_anchors(connection: sqlite3.Connection) -> None:
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
    _sqlite_add_column(connection, "anchors", "client_summary_json", "TEXT")


def _migrate_postgres_anchors(connection: Any) -> None:
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
        DELETE FROM anchors AS anchor
        USING (
          SELECT
            ctid,
            ROW_NUMBER() OVER (
              PARTITION BY anchor_hash, client_sequence
              ORDER BY receipt_id
            ) AS duplicate_index
          FROM anchors
          WHERE ledger_id IS NULL
        ) AS duplicates
        WHERE anchor.ctid = duplicates.ctid
          AND duplicates.duplicate_index > 1
        """
    )
    connection.execute("UPDATE anchors SET ledger_id = '' WHERE ledger_id IS NULL")
    connection.execute("ALTER TABLE anchors ADD COLUMN IF NOT EXISTS client_summary_json TEXT")


def _migrate_sqlite_cloud(connection: sqlite3.Connection) -> None:
    _sqlite_add_column(connection, "api_tokens", "last_used_at", "TEXT")
    _sqlite_add_column(connection, "api_tokens", "revoked_at", "TEXT")
    _sqlite_add_column(connection, "cloud_ledgers", "last_anchor_hash", "TEXT")
    _sqlite_add_column(connection, "cloud_ledgers", "last_sequence", "INTEGER")
    _sqlite_add_column(connection, "cloud_ledgers", "last_seen_at", "TEXT")


def _migrate_postgres_cloud(connection: Any) -> None:
    connection.execute("ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS last_used_at TEXT")
    connection.execute("ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS revoked_at TEXT")
    connection.execute("ALTER TABLE cloud_ledgers ADD COLUMN IF NOT EXISTS last_anchor_hash TEXT")
    connection.execute("ALTER TABLE cloud_ledgers ADD COLUMN IF NOT EXISTS last_sequence BIGINT")
    connection.execute("ALTER TABLE cloud_ledgers ADD COLUMN IF NOT EXISTS last_seen_at TEXT")


def _sqlite_add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
