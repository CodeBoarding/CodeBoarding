from abc import ABC, abstractmethod
from pathlib import Path
from typing import TypeVar, Generic

from utils import get_cache_dir

T = TypeVar("T")
C = TypeVar("C")


class BaseCache(ABC, Generic[T, C]):
    def __init__(self, repo_dir: Path, filename: str):
        self.cache_dir = get_cache_dir(repo_dir)
        self.file_path = self.cache_dir / filename
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def load(self) -> T | None:
        pass

    @abstractmethod
    def store(self, data: T) -> None:
        pass

    @abstractmethod
    def signature(self, context: C | None = None) -> str:
        pass

    @abstractmethod
    def is_stale(self, data: T, context: C | None = None) -> bool:
        pass
