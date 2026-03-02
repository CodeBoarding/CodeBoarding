import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Generic, TypeVar

from utils import get_cache_dir

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, delete, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

K = TypeVar("K", bound=BaseModel)
V = TypeVar("V", bound=BaseModel)
CACHE_VERSION = 1
_DEFAULT_NAMESPACE = "__default__"
_CACHE_TABLE = "cache_entries"

logger = logging.getLogger(__name__)


class BaseCache(Generic[K, V]):
    """Minimal key/value cache interface."""

    _CLEAR_BEFORE_STORE = True
    _REQUIRE_RUN_ID = False

    def __init__(
        self,
        filename: str,
        value_type: type[V],
        repo_dir: Path,
        namespace: str | None = None,
    ):
        self.cache_dir = get_cache_dir(repo_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.cache_dir / filename
        self._value_type = value_type
        self._namespace = namespace
        self._sqlite_cache: Engine | None = None
        self._init_lock = threading.Lock()
        self._metadata = MetaData()
        self._cache_entries = Table(
            _CACHE_TABLE,
            self._metadata,
            Column("namespace", String, primary_key=True),
            Column("key_sig", String, primary_key=True),
            Column("run_id", String, nullable=False),
            Column("value_json", String, nullable=False),
            Column("updated_at", Integer, nullable=False),
            Index(f"idx_{_CACHE_TABLE}_namespace", "namespace"),
            Index(f"idx_{_CACHE_TABLE}_namespace_run_id", "namespace", "run_id"),
        )
        self._sqlite_disabled = False

    def _open_sqlite(self) -> Engine | None:
        if self._sqlite_disabled:
            return None

        if self._sqlite_cache is not None:
            return self._sqlite_cache

        with self._init_lock:
            if self._sqlite_disabled:
                return None
            if self._sqlite_cache is not None:
                return self._sqlite_cache

            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                engine = create_engine(f"sqlite:///{self.file_path}", future=True)
                self._metadata.create_all(engine)
                self._reset_if_incompatible_schema(engine)
                self._sqlite_cache = engine
                logger.info("Cache initialized at %s", self.file_path)
                return self._sqlite_cache
            except (OSError, SQLAlchemyError) as e:
                logger.warning("Cache disabled: %s", e)
                self._sqlite_disabled = True
                return None

    def _reset_if_incompatible_schema(self, engine: Engine) -> None:
        """Reset cache table if schema doesn't match the strict expected columns."""
        with engine.connect() as conn:
            columns = conn.execute(text(f"PRAGMA table_info({_CACHE_TABLE})")).all()
        if not columns:
            return

        expected = {"namespace", "key_sig", "run_id", "value_json", "updated_at"}
        actual = {str(row[1]) for row in columns}
        if actual == expected:
            return

        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {_CACHE_TABLE}"))
        self._metadata.create_all(engine)

    def signature(self, payload: K) -> str:
        encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _resolve_namespace(self, namespace: str | None = None) -> str:
        if namespace is not None:
            return namespace
        if self._namespace is not None:
            return self._namespace
        return _DEFAULT_NAMESPACE

    def _lookup(self, key_sig: str, namespace: str | None = None) -> str | None:
        engine = self._open_sqlite()
        if engine is None:
            return None

        ns = self._resolve_namespace(namespace)
        stmt = (
            select(self._cache_entries.c.value_json)
            .where(
                self._cache_entries.c.namespace == ns,
                self._cache_entries.c.key_sig == key_sig,
            )
            .limit(1)
        )
        with engine.connect() as conn:
            value_json = conn.execute(stmt).scalar_one_or_none()
        return str(value_json) if value_json is not None else None

    def _upsert(self, key_sig: str, value_json: str, run_id: str, namespace: str | None = None) -> None:
        engine = self._open_sqlite()
        if engine is None:
            return

        ns = self._resolve_namespace(namespace)
        updated_at = time.time_ns()
        stmt = insert(self._cache_entries).values(
            namespace=ns,
            key_sig=key_sig,
            run_id=run_id,
            value_json=value_json,
            updated_at=updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["namespace", "key_sig"],
            set_={"run_id": run_id, "value_json": value_json, "updated_at": updated_at},
        )
        with engine.begin() as conn:
            conn.execute(stmt)

    def _clear_namespace(self, namespace: str | None = None, keep_run_ids: list[str] | None = None) -> int:
        engine = self._open_sqlite()
        if engine is None:
            return 0

        ns = self._resolve_namespace(namespace)
        if keep_run_ids:
            stmt = delete(self._cache_entries).where(
                self._cache_entries.c.namespace == ns,
                self._cache_entries.c.run_id.not_in(keep_run_ids),
            )
        else:
            stmt = delete(self._cache_entries).where(self._cache_entries.c.namespace == ns)
        with engine.begin() as conn:
            result = conn.execute(stmt)
        return int(result.rowcount or 0)

    def load(self, key: K) -> V | None:
        try:
            key_signature = self.signature(key)
            value_json = self._lookup(key_signature)
            if value_json is None:
                logger.info("Cache miss: %s key=%s", self.file_path.name, key_signature)
                return None
            value = self._value_type.model_validate_json(value_json)
            logger.info("Cache load success: %s key=%s", self.file_path.name, key_signature)
            return value
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: K, value: V, run_id: str = "") -> None:
        try:
            if self._REQUIRE_RUN_ID and not run_id:
                raise ValueError("run_id is required for this cache")
            key_sig = self.signature(key)
            if self._CLEAR_BEFORE_STORE:
                self._clear_namespace()
            self._upsert(key_sig, value.model_dump_json(), run_id=run_id)
            logger.info("Cache store success: %s key=%s", self.file_path.name, key_sig)
        except Exception as e:
            logger.warning("Cache store failed: %s", e)

    def clear(self, keep_run_ids: list[str] | None = None) -> int:
        try:
            return self._clear_namespace(keep_run_ids=keep_run_ids)
        except Exception as e:
            logger.warning("Cache clear failed: %s", e)
            return 0

    def load_most_recent_run(self, namespace: str) -> tuple[str, int] | None:
        try:
            engine = self._open_sqlite()
            if engine is None:
                return None

            latest = func.max(self._cache_entries.c.updated_at)
            stmt = (
                select(self._cache_entries.c.run_id, latest.label("latest_updated_at"))
                .where(
                    self._cache_entries.c.namespace == namespace,
                    self._cache_entries.c.run_id != "",
                )
                .group_by(self._cache_entries.c.run_id)
                .order_by(latest.desc())
                .limit(1)
            )
            with engine.connect() as conn:
                row = conn.execute(stmt).first()
            if row is None or row[0] is None or row[1] is None:
                return None
            return str(row[0]), int(row[1])
        except Exception as e:
            logger.warning("Cache latest run_id load failed for namespace=%s: %s", namespace, e)
            return None

    def is_stale(self, key: K) -> bool:
        try:
            return self._lookup(self.signature(key)) is None
        except Exception as e:
            logger.warning("Cache staleness check failed: %s", e)
            return True

    def close(self) -> None:
        cache = self._sqlite_cache
        if cache is None:
            return

        try:
            cache.dispose()
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
    def from_chat_model(cls, provider: str, llm: BaseChatModel) -> "ModelSettings":
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
