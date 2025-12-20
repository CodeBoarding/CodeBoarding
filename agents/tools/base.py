import os
from pathlib import Path
from typing import Optional, List
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults


class RepoContext(BaseModel):
    """
    Encapsulates shared dependencies for repository-aware tools.
    """

    repo_dir: Path
    ignore_manager: RepoIgnoreManager
    static_analysis: Optional[StaticAnalysisResults] = None
    # Shared caches to prevent redundant filesystem walks
    _file_cache: List[Path] = PrivateAttr(default_factory=list)
    _dir_cache: List[Path] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def get_files(self) -> List[Path]:
        """Returns a cached list of all non-ignored files."""
        if not self._file_cache:
            self._ensure_cache()
        return self._file_cache

    def get_directories(self) -> List[Path]:
        """Returns a cached list of all non-ignored directories."""
        if not self._dir_cache:
            self._ensure_cache()
        return self._dir_cache

    def _ensure_cache(self):
        self._file_cache = []
        self._dir_cache = [self.repo_dir]
        self._perform_walk(self.repo_dir)

    def _perform_walk(self, current_dir: Path):
        try:
            for entry in os.listdir(current_dir):
                path = current_dir / entry
                if self.ignore_manager.should_ignore(path):
                    continue
                if path.is_file():
                    self._file_cache.append(path)
                elif path.is_dir():
                    self._dir_cache.append(path)
                    self._perform_walk(path)
        except (PermissionError, FileNotFoundError):
            pass


class BaseRepoTool(BaseTool):
    """
    Base class for all tools that interact with a repository.
    Standardizes how tools access repository context and common utilities.
    """

    context: RepoContext = Field(description="The repository context containing shared dependencies.")

    class Config:
        arbitrary_types_allowed = True

    @property
    def repo_dir(self) -> Path:
        return self.context.repo_dir

    @property
    def ignore_manager(self) -> RepoIgnoreManager:
        return self.context.ignore_manager

    @property
    def static_analysis(self) -> Optional[StaticAnalysisResults]:
        return self.context.static_analysis

    def is_subsequence(self, sub: Path, full: Path) -> bool:
        """
        Helper to check if 'sub' is a logical part of 'full' path,
        relative to the repository root.
        """
        sub_parts = sub.parts
        full_parts = full.parts
        repo_dir_parts = self.repo_dir.parts

        # Strip repo root prefix from the full path for comparison
        if full.is_relative_to(self.repo_dir):
            full_parts = full_parts[len(repo_dir_parts) :]

        for i in range(len(full_parts) - len(sub_parts) + 1):
            if full_parts[i : i + len(sub_parts)] == sub_parts:
                return True
        return False
