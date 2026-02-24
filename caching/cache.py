import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

K = TypeVar("K")
V = TypeVar("V")


class BaseCache(ABC, Generic[K, V]):
    """Minimal key/value cache interface."""

    def __init__(self, filename: str, cache_dir: Path):
        self.cache_dir = cache_dir
        self.file_path = self.cache_dir / filename
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def load(self, key: K) -> V | None:
        pass

    @abstractmethod
    def store(self, key: K, value: V) -> None:
        pass

    @abstractmethod
    def signature(self, key: K) -> str:
        pass

    @abstractmethod
    def is_stale(self, key: K, value: V) -> bool:
        pass


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
