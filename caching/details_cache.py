import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from caching.cache import BaseCache

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class DetailsCacheRecord(BaseModel):
    request_signature: str
    payload_json: str


class DetailsCache(BaseCache[DetailsCacheRecord, str]):
    """Single-slot cache for DetailsAgent outputs.

    Each store operation overwrites the previous entry. A cache entry is stale
    when the request signature differs.
    """

    _io_lock = threading.Lock()

    def __init__(self, repo_dir: Path, agent_llm: BaseChatModel, parsing_llm: BaseChatModel):
        super().__init__(repo_dir, "details_agent_single.json")
        self._model_signature = self._build_model_signature(agent_llm, parsing_llm)

    def _build_model_signature(self, agent_llm: BaseChatModel, parsing_llm: BaseChatModel) -> str:
        payload = {
            "kind": "details_agent",
            "agent": agent_llm._get_llm_string(),
            "parser": parsing_llm._get_llm_string(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def load(self) -> DetailsCacheRecord | None:
        if not self.file_path.exists():
            return None
        try:
            with self._io_lock:
                return DetailsCacheRecord.model_validate_json(self.file_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Details cache read failed: %s", e)
            return None

    def store(self, data: DetailsCacheRecord) -> None:
        temp_file = self.file_path.with_suffix(".tmp")
        try:
            with self._io_lock:
                temp_file.write_text(data.model_dump_json(), encoding="utf-8")
                temp_file.replace(self.file_path)
        except Exception as e:
            logger.warning("Details cache write failed: %s", e)

    def signature(self, context: str | None = None) -> str:
        if context is None:
            return self._model_signature
        payload = {
            "prompt_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
            "model_signature": self._model_signature,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    def is_stale(self, data: DetailsCacheRecord, context: str | None = None) -> bool:
        if context is None:
            return True
        return data.request_signature != self.signature(context)

    def get(self, prompt: str, response_model: type[T]) -> T | None:
        record = self.load()
        if record is None:
            return None
        if self.is_stale(record, prompt):
            return None
        try:
            return response_model.model_validate_json(record.payload_json)
        except Exception as e:
            logger.warning("Details cache payload parse failed: %s", e)
            return None

    def put(self, prompt: str, payload: BaseModel) -> None:
        self.store(
            DetailsCacheRecord(
                request_signature=self.signature(prompt),
                payload_json=payload.model_dump_json(),
            )
        )

    def clear(self) -> None:
        try:
            with self._io_lock:
                if self.file_path.exists():
                    self.file_path.unlink()
        except Exception as e:
            logger.warning("Details cache clear failed: %s", e)
