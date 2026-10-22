"""SQLite persistence and schema migration.

One process owns the database.  WAL mode plus a process-wide lock keeps
transactions short and serialized; this is not a multi-replica design.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, cast

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    scopes TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    last_rotated_at TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS policies (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    masked TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    config TEXT NOT NULL,
    credential_id TEXT,
    status TEXT NOT NULL DEFAULT 'unconfigured',
    last_check_at TEXT,
    last_ok_at TEXT,
    last_check_message TEXT,
    last_check_detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    connection_id TEXT,
    request_hash TEXT NOT NULL,
    params_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    used_at TEXT,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    connection_id TEXT,
    status TEXT NOT NULL,
    params_summary TEXT NOT NULL,
    result_summary TEXT,
    error_code TEXT,
    approval_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    agent_id TEXT,
    connection_id TEXT,
    tool TEXT,
    params_summary TEXT NOT NULL DEFAULT '{}',
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    approval_id TEXT,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    error_category TEXT,
    request_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit(agent_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_operations_agent ON operations(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agents_token_hash ON agents(token_hash);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)
            self._ensure_schema_version()
        # The database contains hashes, policies, and audit data, not vault
        # secrets; still keep it owner-only.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _ensure_schema_version(self) -> None:
        from ..utils import iso

        row = self._conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._ensure_v2_columns()
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('migrated_at', ?)",
                (iso(),),
            )
            return

        current_version = int(row["value"])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError("database schema is newer than this gateway release")
        if current_version < SCHEMA_VERSION:
            self._ensure_v2_columns()
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('migrated_from', ?)",
                (str(current_version),),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('migrated_at', ?)",
                (iso(),),
            )
            self._conn.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
        else:
            self._ensure_v2_columns()

    def _ensure_v2_columns(self) -> None:
        agent_cols = {str(r["name"]) for r in self._conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "last_rotated_at" not in agent_cols:
            self._conn.execute("ALTER TABLE agents ADD COLUMN last_rotated_at TEXT")
            self._conn.execute("UPDATE agents SET last_rotated_at = created_at WHERE last_rotated_at IS NULL")

        conn_cols = {str(r["name"]) for r in self._conn.execute("PRAGMA table_info(connections)").fetchall()}
        if "last_ok_at" not in conn_cols:
            self._conn.execute("ALTER TABLE connections ADD COLUMN last_ok_at TEXT")
            self._conn.execute(
                "UPDATE connections SET last_ok_at = last_check_at WHERE status = 'ok' AND last_ok_at IS NULL"
            )
        if "last_check_detail" not in conn_cols:
            self._conn.execute(
                "ALTER TABLE connections ADD COLUMN last_check_detail TEXT NOT NULL DEFAULT '{}'"
            )

    def schema_info(self) -> dict[str, Any]:
        rows = self.query_all("SELECT key, value FROM schema_meta")
        meta = {str(r["key"]): str(r["value"]) for r in rows}
        return {
            "version": int(meta.get("schema_version", SCHEMA_VERSION)),
            "target_version": SCHEMA_VERSION,
            "migrated_from": int(meta["migrated_from"]) if "migrated_from" in meta else None,
            "migrated_at": meta.get("migrated_at"),
            "status": "up_to_date",
        }

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return cursor.rowcount

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return cast(sqlite3.Row | None, self._conn.execute(sql, params).fetchone())

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class SettingsStore:
    """Small key/value store for runtime settings controlled by an admin."""

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "emergency_disabled": False,
        "auto_unlock": False,
        "enabled_tool_groups": ["home_assistant", "proxmox", "ssh"],
        "audit_retention_days": 30,
        "approval_ttl_seconds": 600,
        "max_operation_timeout_seconds": 120,
        "max_output_bytes": 262144,
        "ui_theme": "system",
    }
    ALLOWED_KEYS = frozenset(DEFAULTS)

    def __init__(self, db: Database) -> None:
        self.db = db

    def _from_row(self, key: str, raw: str | None) -> Any:
        if raw is None:
            return self.DEFAULTS[key]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return self.DEFAULTS[key]

    def get(self, key: str) -> Any:
        if key not in self.ALLOWED_KEYS:
            raise KeyError(key)
        row = self.db.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return self._from_row(key, row["value"] if row else None)

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_int(self, key: str) -> int:
        value = self.get(key)
        return (
            int(value) if isinstance(value, int) and not isinstance(value, bool) else int(self.DEFAULTS[key])
        )

    def set(self, key: str, value: Any) -> None:
        if key not in self.ALLOWED_KEYS:
            raise KeyError(key)
        from ..utils import iso

        self.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), iso()),
        )

    def all_public(self) -> dict[str, Any]:
        return {key: self.get(key) for key in sorted(self.ALLOWED_KEYS)}
