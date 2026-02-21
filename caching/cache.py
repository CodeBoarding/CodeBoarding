from abc import ABC, abstractmethod
from pathlib import Path
from typing import TypeVar, Generic

T = TypeVar("T")


class BaseCache(ABC, Generic[T]):

    def __init__(self, filename: str, cache_dir: Path):
        self.cache_dir = cache_dir
        self.file_path = self.cache_dir / filename
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def load(self) -> T | None:
        pass

    @abstractmethod
    def store(self, data: T) -> None:
        pass

    @abstractmethod
    def signature(self) -> str:
        pass

    @abstractmethod
    def is_stale(self, data: T) -> bool:
        pass
