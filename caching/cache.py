import hashlib
import json
import logging
import pickle
import sqlite3
from abc import ABC
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

K = TypeVar("K")
V = TypeVar("V")

logger = logging.getLogger(__name__)


class BaseCache(ABC, Generic[K, V]):
    """Minimal key/value cache interface."""

    def __init__(self, filename: str, cache_dir: Path):
        self.cache_dir = cache_dir
        self.file_path = self.cache_dir / filename
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.file_path)
        conn.execute("CREATE TABLE IF NOT EXISTS cache_entries (key_hash TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        return conn

    def signature(self, key: K | None = None) -> str:
        if key is None and hasattr(self, "_prompt_key") and hasattr(self, "_llm_key"):
            payload = {"prompt_key": getattr(self, "_prompt_key"), "llm_key": getattr(self, "_llm_key")}
        else:
            payload = key

        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def load(self, key: K | None = None) -> V | None:
        key_hash = self.signature(key)
        try:
            with self._open_connection() as conn:
                row = conn.execute("SELECT payload FROM cache_entries WHERE key_hash = ?", (key_hash,)).fetchone()
        except sqlite3.Error as e:
            logger.warning("Cache load failed: %s", e)
            return None

        if row is None:
            return None

        try:
            return pickle.loads(row[0])
        except Exception as e:
            logger.warning("Cache payload decode failed: %s", e)
            return None

    def store(self, key: K | V | None, value: V | None = None) -> None:
        if value is None:
            data = key
            cache_key = None
        else:
            data = value
            cache_key = key

        key_hash = self.signature(cache_key)
        try:
            payload = pickle.dumps(data)
            with self._open_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cache_entries(key_hash, payload) VALUES (?, ?)",
                    (key_hash, payload),
                )
                conn.commit()
        except (sqlite3.Error, pickle.PickleError, TypeError) as e:
            logger.warning("Cache store failed: %s", e)

    def is_stale(self, key: K | None = None) -> bool:
        key_hash = self.signature(key)
        try:
            with self._open_connection() as conn:
                row = conn.execute("SELECT 1 FROM cache_entries WHERE key_hash = ?", (key_hash,)).fetchone()
            return row is None
        except sqlite3.Error as e:
            logger.warning("Cache staleness check failed: %s", e)
            return True


class ModelSettings(BaseModel):
    """Stable snapshot of resolved model config for cache invalidation."""

    model_config = ConfigDict(frozen=True)

    provider: str
    chat_class: str
    model_name: str
    base_url: str | None = None
    max_tokens: int | None = None
    max_retries: int | None = None
    timeout: float | None = None

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def signature(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class DetailsCacheRecord(BaseModel):
    prompt: str
    model_name: str
    model_settings: ModelSettings

    @property
    def model_signature(self) -> str:
        return self.model_settings.signature()


class MetaCacheRecord(BaseModel):
    prompt: str
    model: str
    model_settings: ModelSettings
    metadata_files: list[str]
    metadata_content_hash: str

    @property
    def model_signature(self) -> str:
        return self.model_settings.signature()
