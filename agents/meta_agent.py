import logging
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import LargeModelAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class MetaAgent(LargeModelAgent):
    _DOC_SUFFIXES = {".md", ".rst", ".txt", ".html"}
    _DEPENDENCY_FILES = {
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "dev-requirements.txt",
        "test-requirements.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "environment.yml",
        "environment.yaml",
        "conda.yml",
        "conda.yaml",
        "pixi.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "tsconfig.json",
    }
    _DEPENDENCY_DIRS = {"requirements", "deps", "dependencies", "env"}
    _DEPENDENCY_DIR_SUFFIXES = {".txt", ".yml", ".yaml", ".toml"}

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults | None, project_name: str):
        super().__init__(repo_dir, static_analysis, get_system_meta_analysis_message())
        self.project_name = project_name
        self.meta_cache_enabled = os.getenv("CODEBOARDING_META_CACHE", "true").lower() in {"1", "true", "yes"}
        self.meta_cache_path = self.repo_dir / ".codeboarding" / "cache" / "meta_analysis_cache.json"

        self.meta_analysis_prompt = PromptTemplate(
            template=get_meta_information_prompt(), input_variables=["project_name"]
        )

        self.agent = create_react_agent(
            model=self.llm,
            tools=[self.toolkit.read_docs, self.toolkit.external_deps, self.toolkit.read_file_structure],
        )

    @trace
    def analyze_project_metadata(self) -> MetaAnalysisInsights:
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        cache_key = None
        if self.meta_cache_enabled:
            try:
                cache_key = self._compute_meta_cache_key()
                cached = self._load_cached_meta(cache_key)
                if cached is not None:
                    logger.info("[MetaAgent] Using cached metadata analysis")
                    return cached
            except Exception as e:
                logger.warning(f"[MetaAgent] Failed to read meta cache, proceeding without cache: {e}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)

        if self.meta_cache_enabled and cache_key is not None:
            try:
                self._save_cached_meta(cache_key, analysis)
            except Exception as e:
                logger.warning(f"[MetaAgent] Failed to write meta cache: {e}")

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis

    def _compute_meta_cache_key(self) -> str:
        hasher = hashlib.sha256()

        hasher.update(f"project_name:{self.project_name}\n".encode("utf-8"))
        hasher.update(f"system_prompt:{get_system_meta_analysis_message()}\n".encode("utf-8"))
        hasher.update(f"meta_prompt:{get_meta_information_prompt()}\n".encode("utf-8"))

        model_hint = (
            getattr(self.llm, "model_name", None)
            or getattr(self.llm, "model", None)
            or getattr(self.llm, "model_id", None)
            or self.llm.__class__.__name__
        )
        hasher.update(f"model_hint:{model_hint}\n".encode("utf-8"))

        files = self.toolkit.read_docs.context.get_files()
        dirs = self.toolkit.read_docs.context.get_directories()

        rel_dirs = sorted(str(d.relative_to(self.repo_dir)) for d in dirs if d != self.repo_dir)
        rel_files = sorted(str(f.relative_to(self.repo_dir)) for f in files)

        for rel_dir in rel_dirs:
            hasher.update(f"D:{rel_dir}\n".encode("utf-8"))
        for rel_file in rel_files:
            hasher.update(f"F:{rel_file}\n".encode("utf-8"))

        meta_content_files = self._select_meta_content_files(files)
        for file_path in meta_content_files:
            rel_path = str(file_path.relative_to(self.repo_dir))
            hasher.update(f"C:{rel_path}\n".encode("utf-8"))
            try:
                content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                hasher.update(content_hash.encode("utf-8"))
            except Exception:
                stat = file_path.stat()
                hasher.update(f"STAT:{stat.st_size}:{int(stat.st_mtime)}\n".encode("utf-8"))

        return hasher.hexdigest()

    def _select_meta_content_files(self, files: list[Path]) -> list[Path]:
        selected: list[Path] = []
        for file_path in files:
            rel_parts = file_path.relative_to(self.repo_dir).parts
            suffix = file_path.suffix.lower()
            name = file_path.name

            is_doc = suffix in self._DOC_SUFFIXES and "tests" not in rel_parts and "test" not in name.lower()
            is_root_dep = name in self._DEPENDENCY_FILES
            is_dep_dir_file = (
                len(rel_parts) > 1 and rel_parts[0] in self._DEPENDENCY_DIRS and suffix in self._DEPENDENCY_DIR_SUFFIXES
            )

            if is_doc or is_root_dep or is_dep_dir_file:
                selected.append(file_path)

        return sorted(set(selected))

    def _load_cached_meta(self, cache_key: str) -> MetaAnalysisInsights | None:
        if not self.meta_cache_path.exists():
            return None

        payload = json.loads(self.meta_cache_path.read_text(encoding="utf-8"))
        entry = payload.get("entries", {}).get(cache_key)
        if not entry:
            return None

        return MetaAnalysisInsights.model_validate(entry["analysis"])

    def _save_cached_meta(self, cache_key: str, analysis: MetaAnalysisInsights) -> None:
        self.meta_cache_path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict = {"version": 1, "entries": {}}
        if self.meta_cache_path.exists():
            payload = json.loads(self.meta_cache_path.read_text(encoding="utf-8"))
            payload.setdefault("version", 1)
            payload.setdefault("entries", {})

        payload["entries"][cache_key] = {
            "project_name": self.project_name,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "analysis": analysis.model_dump(mode="json"),
        }

        temp_file = self.meta_cache_path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.meta_cache_path)
