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

    def __init__(self, db_path: Path, table_name: str):
        self.db_path = db_path
        self.table_name = _validate_identifier(table_name)
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
                    PRIMARY KEY(scope, cache_key)
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table_name}_scope_time
                ON {self.table_name}(scope, updated_at DESC)
                """
            )

    def load_latest(self, scope: str) -> tuple[str, dict[str, Any]] | None:
        with self._conn() as conn:
            row = conn.execute(
                f"""
                SELECT payload_json, meta_json
                FROM {self.table_name}
                WHERE scope=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (scope,),
            ).fetchone()
        if row is None:
            return None
        payload_json, meta_json = row
        return payload_json, json.loads(meta_json)

    def upsert(self, scope: str, cache_key: str, payload_json: str, meta: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {self.table_name}(scope, cache_key, payload_json, meta_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (scope, cache_key, payload_json, json.dumps(meta), int(time.time())),
            )
