"""TypeScript / JavaScript project discovery.

Resolves each project's owned file list authoritatively via ``tsc --showConfig``
so that nested ``tsconfig.json`` files in a monorepo don't double-claim files.
Why: a "solution" tsconfig at the repo root (``files: []`` + ``references: [...]``)
must contribute zero files itself — otherwise its filesystem walk overlaps with
the leaf projects it points at, and every overlapping file ends up analyzed
twice under two different qualified-name roots, inflating reference and
package counts.
"""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from tool_registry.paths import get_servers_dir, preferred_node_path

logger = logging.getLogger(__name__)


@dataclass
class TypeScriptProject:
    """One TS/JS project the engine should analyze.

    ``files`` holds absolute paths resolved by ``tsc --showConfig`` (or by a
    filesystem fallback if tsc is unavailable). The engine should send exactly
    these to the LSP client rooted at ``root`` — anything broader would re-claim
    files that belong to a sibling project.
    """

    root: Path
    files: list[Path] = field(default_factory=list)


_TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
_ALL_EXTENSIONS = _TS_EXTENSIONS + _JS_EXTENSIONS


class TypeScriptConfigScanner:
    """Discovers TS/JS project roots and their owned files for monorepo support."""

    CONFIG_FILES = ["tsconfig.json", "jsconfig.json"]

    def __init__(self, repo_location: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_location = repo_location.resolve()
        self.ignore_manager = ignore_manager or RepoIgnoreManager(repo_location)

    def find_typescript_projects(self) -> list[TypeScriptProject]:
        """Return one project per leaf tsconfig with its authoritative file list.

        Strategy (two-tier resolution + trim):
        1. ``rglob`` the repo for ``tsconfig.json`` / ``jsconfig.json`` (skipping
           ignored paths). This is the candidate set.
        2. **Option A**: ask ``tsc --showConfig`` what files each candidate owns.
           Authoritative because tsc honours ``files``/``include``/``exclude``
           semantics. The result is then filtered through ``ignore_manager``
           so ``.codeboardingignore`` patterns (e.g. ``*.test.*``) apply
           consistently with Option B.
        3. **Option B (fallback)**: if tsc is unavailable or fails for a
           candidate, fall back to a filesystem walk that explicitly excludes
           any subtree owned by another candidate and applies the ignore
           manager. Less precise than tsc (no ``include``/``exclude``
           honour) but each file is still claimed at most once.
        4. ``_trim_overlap`` then assigns each file to exactly one project —
           the deepest candidate that claims it. Files that only the root
           tsconfig sees (e.g. ``excalidraw-app/`` in a monorepo) survive
           on the root project; files claimed by both root and a leaf go
           to the leaf. A project whose entire claim is covered by deeper
           projects (a pure "solution" tsconfig) is dropped.
        """
        candidate_dirs = self._discover_candidates()
        if not candidate_dirs:
            logger.warning(f"No TypeScript configuration files found in {self.repo_location}")
            return []

        node_path = preferred_node_path(get_servers_dir())
        tsc_js = _resolve_tsc_js()
        tsc_cmd_prefix = _build_tsc_command_prefix(node_path, tsc_js)
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
                # No owned files even before trim — already a solution
                # tsconfig (or every file ignored). Either way, no work
                # for this project.
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

    # ------------------------------------------------------------------ #
    # Candidate discovery
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # File resolution per project (tsc --showConfig with FS fallback)
    # ------------------------------------------------------------------ #

    def _resolve_project_files(
        self,
        project_dir: Path,
        tsc_cmd_prefix: list[str] | None,
        all_candidates: list[Path],
    ) -> list[Path]:
        if tsc_cmd_prefix is not None:
            config = self._showconfig(project_dir, tsc_cmd_prefix)
            if config is not None:
                files = [_normalize(project_dir, raw) for raw in config.get("files", []) if isinstance(raw, str)]
                # Apply ignore_manager too: tsc honours each tsconfig's own
                # include/exclude, but a permissive root tsconfig can claim
                # files (e.g. ``*.test.ts`` in packages/) that the user has
                # asked CodeBoarding to skip via .codeboardingignore. Without
                # this filter, Option A would re-introduce noise that Option B
                # already drops via ``_fallback_walk``.
                return [f for f in files if f.suffix in _ALL_EXTENSIONS and not self.ignore_manager.should_ignore(f)]
            # tsc was available but failed for THIS project — fall through
            # to a filesystem walk for this one project specifically.
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

        Walks ``project_dir`` and skips any subtree owned by another candidate
        project. Each TS/JS file ends up in exactly one project (the deepest
        candidate containing it).

        Why disjoint: a naive walk from a parent (e.g. the repo root) would
        re-claim every file already owned by a child (e.g. ``packages/foo/``).
        Both projects would then add overlapping ``source_files`` lists,
        re-introducing the duplicate-counting bug this module exists to fix.
        Less precise than tsc's resolution (no ``include`` / ``exclude``
        semantics) but each file is claimed by exactly one project.
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

    # ------------------------------------------------------------------ #
    # Per-file ownership trim
    # ------------------------------------------------------------------ #

    @staticmethod
    def _trim_overlap(projects: list[TypeScriptProject]) -> list[TypeScriptProject]:
        """Each file is owned by exactly one project — the deepest that claims it.

        Why deepest-first: leaf tsconfigs typically have stricter ``exclude``
        patterns (e.g. ``packages/foo/tsconfig.json`` excludes ``**/*.test.*``)
        and represent the package author's view of "production code." A root
        tsconfig that ``include``s the whole repo overlaps with every leaf;
        attributing shared files to the leaf preserves the leaf's intent.

        A parent that has files NOT claimed by any leaf (e.g. an Excalidraw-
        style root that ``include``s ``packages`` *and* ``excalidraw-app``,
        where no leaf covers ``excalidraw-app/``) keeps its leftover. A parent
        whose entire file set is covered by leaves (a pure solution tsconfig)
        is dropped.

        Determinism: on equal depth, alphabetical order of the absolute path
        breaks ties — so two same-depth siblings claiming the same file (rare,
        weird ``include`` globs) get consistent first-wins behavior across
        runs.
        """
        # Track original index so survivors come out in caller's order.
        indexed = list(enumerate(projects))
        # Deepest first; alphabetical secondary for stable tiebreak.
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


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _normalize(project_dir: Path, raw: str) -> Path:
    """Resolve a tsc-emitted file path (often relative) to an absolute path."""
    p = Path(raw)
    if not p.is_absolute():
        p = project_dir / p
    return p.resolve()


def _is_ancestor(maybe_ancestor: Path, descendant: Path) -> bool:
    try:
        descendant.relative_to(maybe_ancestor)
    except ValueError:
        return False
    return descendant != maybe_ancestor


def _resolve_tsc_js() -> Path | None:
    """Return the bundled ``tsc.js`` shipped with typescript-language-server."""
    candidate = get_servers_dir() / "node_modules" / "typescript" / "lib" / "tsc.js"
    return candidate if candidate.exists() else None


def _build_tsc_command_prefix(node_path: str | None, tsc_js: Path | None) -> list[str] | None:
    """Pick the most reliable way to invoke ``tsc --showConfig`` on this host.

    Preference order:
    1. Embedded ``node`` + bundled ``tsc.js`` (vendored alongside
       typescript-language-server, no PATH assumptions).
    2. System ``tsc`` if it's on PATH.
    Returns ``None`` if neither is available.

    The returned list is the prefix to which the caller appends ``-p <dir>``.
    """
    if node_path and tsc_js and tsc_js.exists():
        return [node_path, str(tsc_js), "--showConfig"]
    system_tsc = _resolve_system_tsc()
    if system_tsc is not None:
        return [system_tsc, "--showConfig"]
    return None


def _resolve_system_tsc() -> str | None:
    """Find a usable ``tsc`` binary on the system PATH.

    Why ``shutil.which`` rather than hand-rolling the loop: on Windows the
    binary may be ``tsc.cmd`` (npm-global), ``tsc.exe`` (some chocolatey/scoop
    shims), or ``tsc.bat``. ``shutil.which`` honors ``PATHEXT`` and resolves
    whichever extension is registered first — matching what a user typing
    ``tsc`` in their shell would get.
    """
    return shutil.which("tsc")
