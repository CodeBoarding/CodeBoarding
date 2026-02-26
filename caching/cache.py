import hashlib
import json
import logging
import sqlite3
from typing import Generic, TypeVar
from utils import get_cache_dir, get_project_root

from langchain_core.language_models import BaseChatModel
from langchain_community.cache import SQLiteCache
from langchain_core.outputs import Generation
from pydantic import BaseModel, ConfigDict

K = TypeVar("K")
V = TypeVar("V", bound=BaseModel)
CACHE_VERSION = 1

logger = logging.getLogger(__name__)


class BaseCache(Generic[K, V]):
    """Minimal key/value cache interface."""

    def __init__(self, filename: str, value_type: type[V]):
        self.cache_dir = get_cache_dir(get_project_root())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.cache_dir / filename
        self._value_type = value_type
        self._sqlite_cache: SQLiteCache | None = None
        self._sqlite_disabled = False

    def _open_sqlite(self) -> SQLiteCache | None:
        if self._sqlite_disabled:
            return None

        if self._sqlite_cache is not None:
            return self._sqlite_cache

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._sqlite_cache = SQLiteCache(database_path=str(self.file_path))
            return self._sqlite_cache
        except (OSError, sqlite3.Error) as e:
            logger.warning("Cache disabled: %s", e)
            self._sqlite_disabled = True
            return None

    def signature(self, payload: object) -> str:
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def load(self, key: K) -> V | None:
        cache = self._open_sqlite()
        if cache is None:
            return None

        try:
            raw = cache.lookup(self.signature(key), None)
            if not raw:
                return None
            return self._value_type.model_validate_json(raw[0].text)
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: K, value: V) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            cache.clear()
            cache.update(self.signature(key), None, [Generation(text=value.model_dump_json())])
        except Exception as e:
            logger.warning("Cache store failed: %s", e)

    def is_stale(self, key: K) -> bool:
        cache = self._open_sqlite()
        if cache is None:
            return True

        try:
            return cache.lookup(self.signature(key), None) is None
        except Exception as e:
            logger.warning("Cache staleness check failed: %s", e)
            return True

    def clear(self) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            cache.clear()
        except Exception as e:
            logger.warning("Cache clear failed: %s", e)

    def close(self) -> None:
        cache = self._sqlite_cache
        if cache is None:
            return

        try:
            cache.engine.dispose()
        except Exception as e:
            logger.warning("Cache close failed: %s", e)
        finally:
            self._sqlite_cache = None


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

    @classmethod
    def from_chat_model(cls, provider: str, llm: BaseChatModel | None) -> "ModelSettings":
        if llm is None:
            return cls(provider=provider, chat_class="NoneType", model_name="unknown")

        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None) or llm.__class__.__name__
        base_url = getattr(llm, "base_url", None)
        max_tokens = getattr(llm, "max_tokens", None)
        max_retries = getattr(llm, "max_retries", None)
        timeout = getattr(llm, "timeout", None)

        return cls(
            provider=provider,
            chat_class=llm.__class__.__name__,
            model_name=str(model_name),
            base_url=base_url if isinstance(base_url, str) else None,
            max_tokens=max_tokens if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) else None,
            max_retries=max_retries if isinstance(max_retries, int) and not isinstance(max_retries, bool) else None,
            timeout=(float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else None),
        )
