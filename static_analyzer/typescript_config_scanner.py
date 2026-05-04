"""TypeScript / JavaScript project discovery.

Resolves each project's owned file list via ``tsc --showConfig`` so that nested
``tsconfig.json`` files in a monorepo don't double-claim files. Why: a "solution"
tsconfig at the repo root (``files: []`` + ``references: [...]``) must contribute
zero files itself, or every overlapping file gets analyzed twice and inflates
reference / package counts.
"""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language
from tool_registry.paths import get_servers_dir, preferred_node_path

logger = logging.getLogger(__name__)


@dataclass
class TypeScriptProject:
    """One TS/JS project the engine should analyze.

    ``files`` are absolute paths the engine should send to the LSP — anything
    broader would re-claim files belonging to a sibling project.
    """

    root: Path
    files: list[Path] = field(default_factory=list)


_ALL_EXTENSIONS = LANGUAGE_EXTENSIONS[Language.TYPESCRIPT] + LANGUAGE_EXTENSIONS[Language.JAVASCRIPT]


class TypeScriptConfigScanner:
    """Discovers TS/JS project roots and their owned files for monorepo support."""

    CONFIG_FILES = ["tsconfig.json", "jsconfig.json"]

    def __init__(self, repo_location: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_location = repo_location.resolve()
        self.ignore_manager = ignore_manager or RepoIgnoreManager(repo_location)

    def find_typescript_projects(self) -> list[TypeScriptProject]:
        """Return one project per leaf tsconfig with its authoritative file list.

        Strategy: discover candidate tsconfigs, resolve each one's file set via
        ``tsc --showConfig`` (or a disjoint FS walk if tsc is unavailable), then
        ``_trim_overlap`` so each file ends up in exactly one project — the
        deepest that claims it. A project whose entire claim is covered by
        deeper ones (a pure "solution" tsconfig) is dropped.
        """
        candidate_dirs = self._discover_candidates()
        if not candidate_dirs:
            logger.warning(f"No TypeScript configuration files found in {self.repo_location}")
            return []

        tsc_cmd_prefix = _resolve_tsc_command()
        if tsc_cmd_prefix is None:
            logger.warning(
                "tsc not available (neither bundled tsc.js under servers/ nor system tsc on PATH); "
                "falling back to disjoint filesystem walks. File-membership precision will be "
                "lower than tsc --showConfig but each file is still claimed by exactly one project."
            )

        projects: list[TypeScriptProject] = []
        for project_dir in candidate_dirs:
            files = self._resolve_project_files(project_dir, tsc_cmd_prefix, candidate_dirs)
            if not files:
                logger.debug(f"Skipping tsconfig at {project_dir} (no owned files)")
                continue
            projects.append(TypeScriptProject(root=project_dir, files=files))

        projects = self._trim_overlap(projects)

        if projects:
            total_files = sum(len(p.files) for p in projects)
            logger.info(
                f"Found {len(projects)} TypeScript project(s) in repository "
                f"({total_files} total files across all projects)"
            )
        return projects

    def _discover_candidates(self) -> list[Path]:
        seen: set[Path] = set()
        candidates: list[Path] = []
        for config_file in self.CONFIG_FILES:
            for config_path in self.repo_location.rglob(config_file):
                if not config_path.is_file():
                    continue
                if self.ignore_manager.should_ignore(config_path):
                    logger.debug(f"Skipping ignored config file: {config_path}")
                    continue
                project_dir = config_path.parent.resolve()
                if project_dir in seen:
                    continue
                seen.add(project_dir)
                candidates.append(project_dir)
        return candidates

    def _resolve_project_files(
        self,
        project_dir: Path,
        tsc_cmd_prefix: list[str] | None,
        all_candidates: list[Path],
    ) -> list[Path]:
        if tsc_cmd_prefix is not None:
            config = self._showconfig(project_dir, tsc_cmd_prefix)
            if config is not None:
                files = []
                for raw in config.get("files", []):
                    if not isinstance(raw, str):
                        continue
                    p = Path(raw)
                    if not p.is_absolute():
                        p = project_dir / p
                    files.append(p.resolve())
                # Apply ignore_manager: a permissive root tsconfig can claim
                # files (e.g. ``*.test.ts``) the user has skipped via
                # .codeboardingignore — keep that consistent with the FS fallback.
                return [f for f in files if f.suffix in _ALL_EXTENSIONS and not self.ignore_manager.should_ignore(f)]
            # tsc available but failed for this project — try the FS walk.
        return self._fallback_walk(project_dir, all_candidates)

    def _showconfig(self, project_dir: Path, tsc_cmd_prefix: list[str]) -> dict | None:
        """Invoke ``tsc --showConfig -p <dir>`` and return parsed JSON."""
        cmd = [*tsc_cmd_prefix, "-p", str(project_dir)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"tsc --showConfig invocation failed for {project_dir}: {e}")
            return None

        if result.returncode != 0:
            logger.debug(
                f"tsc --showConfig exited {result.returncode} for {project_dir}: " f"{result.stderr.strip()[:200]}"
            )
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.debug(f"tsc --showConfig JSON parse failed for {project_dir}: {e}")
            return None

    def _fallback_walk(self, project_dir: Path, all_candidates: list[Path]) -> list[Path]:
        """Disjoint filesystem walk used when ``tsc --showConfig`` is unavailable.

        Walks ``project_dir`` skipping any subtree owned by another candidate so
        each TS/JS file is claimed exactly once. Less precise than tsc (no
        ``include``/``exclude`` honour) but avoids the double-counting bug.
        """
        nested = [c for c in all_candidates if c != project_dir and _is_ancestor(project_dir, c)]
        files: list[Path] = []
        for path in project_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _ALL_EXTENSIONS:
                continue
            if self.ignore_manager.should_ignore(path):
                continue
            if any(_is_ancestor(n, path) for n in nested):
                continue
            files.append(path.resolve())
        files.sort()
        return files

    @staticmethod
    def _trim_overlap(projects: list[TypeScriptProject]) -> list[TypeScriptProject]:
        """Assign each file to the deepest project that claims it.

        Why deepest-first: leaf tsconfigs typically have stricter ``exclude``
        patterns and represent the package author's view of "production code,"
        so attributing shared files to the leaf preserves that intent. A parent
        with files no leaf covers keeps its leftover; a parent whose entire set
        is covered by leaves (pure solution tsconfig) is dropped. Alphabetical
        path tiebreak on equal depth keeps siblings deterministic across runs.
        """
        indexed = list(enumerate(projects))
        indexed.sort(key=lambda ip: (-len(ip[1].root.parts), str(ip[1].root)))

        claimed: set[Path] = set()
        survivors: list[tuple[int, TypeScriptProject]] = []
        for orig_idx, project in indexed:
            unique = [f for f in project.files if f not in claimed]
            if not unique:
                logger.debug(f"Dropping project {project.root} — all files claimed by deeper projects")
                continue
            if len(unique) < len(project.files):
                logger.debug(f"Trimmed project {project.root} from {len(project.files)} to {len(unique)} files")
            claimed.update(unique)
            survivors.append((orig_idx, TypeScriptProject(root=project.root, files=unique)))

        survivors.sort(key=lambda ip: ip[0])
        return [p for _, p in survivors]


def _is_ancestor(maybe_ancestor: Path, descendant: Path) -> bool:
    try:
        descendant.relative_to(maybe_ancestor)
    except ValueError:
        return False
    return descendant != maybe_ancestor


def _resolve_system_tsc() -> str | None:
    """Find ``tsc`` on PATH via ``shutil.which`` so Windows ``PATHEXT`` is honoured
    (``tsc.cmd``/``tsc.exe``/``tsc.bat`` from various installers all resolve)."""
    return shutil.which("tsc")


def _resolve_tsc_command() -> list[str] | None:
    """Return the prefix for invoking ``tsc --showConfig`` on this host.

    Prefers the embedded ``node`` + bundled ``tsc.js`` (vendored alongside
    typescript-language-server, no PATH assumptions); falls back to ``tsc`` on
    PATH; returns ``None`` if neither is available. The caller appends ``-p <dir>``.
    """
    node_path = preferred_node_path(get_servers_dir())
    bundled_tsc = get_servers_dir() / "node_modules" / "typescript" / "lib" / "tsc.js"
    if node_path and bundled_tsc.exists():
        return [node_path, str(bundled_tsc), "--showConfig"]
    system_tsc = _resolve_system_tsc()
    if system_tsc is not None:
        return [system_tsc, "--showConfig"]
    return None
