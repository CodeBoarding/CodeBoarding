from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _validate_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Invalid SQLite identifier: {identifier}")
    return identifier


class SQLiteCacheStore:
    """Minimal SQLite key-value store for scoped cache records."""

    def __init__(self, db_path: Path, table_name: str, ttl_seconds: int | None = None):
        self.db_path = db_path
        self.table_name = _validate_identifier(table_name)
        self.ttl_seconds = ttl_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    scope TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_accessed_at INTEGER NOT NULL,
                    PRIMARY KEY(scope, cache_key)
                )
                """
            )
            # Backward-compatible migration for existing tables created before
            # last_accessed_at was introduced.
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()}
            if "last_accessed_at" not in columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN last_accessed_at INTEGER")
            conn.execute(
                f"""
                UPDATE {self.table_name}
                SET last_accessed_at = updated_at
                WHERE last_accessed_at IS NULL
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table_name}_scope_time
                ON {self.table_name}(scope, updated_at DESC)
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table_name}_scope_access_time
                ON {self.table_name}(scope, last_accessed_at DESC)
                """
            )

    def _purge_expired_locked(self, conn: sqlite3.Connection, ttl_seconds: int) -> None:
        cutoff = int(time.time()) - ttl_seconds
        conn.execute(
            f"""
            DELETE FROM {self.table_name}
            WHERE COALESCE(last_accessed_at, updated_at) < ?
            """,
            (cutoff,),
        )

    def load_latest(self, scope: str, ttl_seconds: int | None = None) -> tuple[str, str, dict[str, Any]] | None:
        effective_ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        with self._conn() as conn:
            if effective_ttl is not None:
                self._purge_expired_locked(conn, effective_ttl)
            row = conn.execute(
                f"""
                SELECT cache_key, payload_json, meta_json
                FROM {self.table_name}
                WHERE scope=?
                ORDER BY last_accessed_at DESC, updated_at DESC
                LIMIT 1
                """,
                (scope,),
            ).fetchone()
        if row is None:
            return None
        cache_key, payload_json, meta_json = row
        return cache_key, payload_json, json.loads(meta_json)

    def upsert(self, scope: str, cache_key: str, payload_json: str, meta: dict[str, Any]) -> None:
        now = int(time.time())
        with self._conn() as conn:
            if self.ttl_seconds is not None:
                self._purge_expired_locked(conn, self.ttl_seconds)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {self.table_name}(
                    scope, cache_key, payload_json, meta_json, updated_at, last_accessed_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scope, cache_key, payload_json, json.dumps(meta), now, now),
            )

    def touch(self, scope: str, cache_key: str) -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                f"""
                UPDATE {self.table_name}
                SET last_accessed_at=?
                WHERE scope=? AND cache_key=?
                """,
                (now, scope, cache_key),
            )
