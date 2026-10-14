import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from infra.repo_utils.git_ops import get_changed_files_since
from infra.repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import AdapterName, Language
from static_analyzer.csharp_config_scanner import SOLUTION_GLOBS, CSharpConfigScanner, CSharpProjectConfig
from static_analyzer.dotnet_solution import solution_projects
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.hierarchy_builder import link_declared_bases
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import SERVER_NON_HEAP_BYTES
from static_analyzer.engine.lsp_recycler import per_engine_memory_budget
from static_analyzer.engine.models import ExternalCallSite
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import uri_to_path
from static_analyzer.exceptions import StaticAnalysisFatalError
from static_analyzer.external_calls import link_external_call_sites, record_package_imports
from static_analyzer.graph_definitions import GraphIndex
from static_analyzer.incremental_orchestrator import update_cfg_for_changed_files
from static_analyzer.java_config_scanner import JavaConfigScanner
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from static_analyzer.workspace_dependencies import install_workspace_dependencies
from infra.monitoring.telemetry.events import track_lsp_result
from infra.tool_registry import ensure_node_on_path
from infra.utils import get_artifact_dir

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """One adapter + project root the engine should run.

    ``source_files`` is non-empty only when a scanner has authoritatively
    resolved file membership (TypeScript via ``tsc --showConfig``, C# by what
    each root's solution lists); otherwise the adapter walks ``project_path``
    itself in ``_run_full_analysis``. A config whose membership resolved to no
    file is not created at all.
    """

    adapter: LanguageAdapter
    project_path: Path
    source_files: list[Path] = field(default_factory=list)
    # Retain discovery exclusions for incremental edits, including deleted files.
    excluded_roots: list[Path] = field(default_factory=list)
    # Configs a full pass or full re-analysis runs in its place, each on a server of its own.
    split: list["EngineConfig"] = field(default_factory=list)


TYPESCRIPT_SPLIT_MIN_FILES = 1000
"""TypeScript files from which the full pass runs one server per tsconfig project; below it one server starts once."""

MAX_CONCURRENT_ENGINES_ENV_VAR = "CODEBOARDING_MAX_CONCURRENT_ENGINES"
LSP_REQUEST_TIMEOUT_ENV_VAR = "CODEBOARDING_LSP_REQUEST_TIMEOUT"


def max_concurrent_engines() -> int:
    """How many engine LSP servers an analysis may run at once; 0 when unset.

    Why: when set, no server stays resident, so the editor's file queries refuse (see ``_live_clients``).
    """
    raw = os.environ.get(MAX_CONCURRENT_ENGINES_ENV_VAR, "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r; the bound stays off", MAX_CONCURRENT_ENGINES_ENV_VAR, raw)
        return 0
    if value < 0:
        logger.warning("Ignoring negative %s=%r; the bound stays off", MAX_CONCURRENT_ENGINES_ENV_VAR, raw)
        return 0
    return value


def lsp_request_timeout_override() -> int | None:
    """Seconds from ``CODEBOARDING_LSP_REQUEST_TIMEOUT``, or None when it is unset.

    Why it raises rather than falling back: the fallback is the adapter ceiling the
    operator set the variable to escape, so the run would time out as if ignored.
    """
    raw = os.environ.get(LSP_REQUEST_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        seconds = int(raw)
    except ValueError:
        raise ValueError(f"{LSP_REQUEST_TIMEOUT_ENV_VAR} must be a whole number of seconds, got {raw!r}") from None
    if seconds <= 0:
        raise ValueError(f"{LSP_REQUEST_TIMEOUT_ENV_VAR} must be a positive number of seconds, got {raw!r}")
    return seconds


def _adapter_names_for(programming_languages: list[ProgrammingLanguage]) -> list[str]:
    """Deduplicated engine-adapter names for the scanner's detected languages.

    Why: the scanner reports TypeScript, TSX, JavaScript and JSX separately, but one server
    serves them all, and one adapter per detected name indexes the same files into separate
    buckets that then cluster as separate codebases.
    """
    names: list[str] = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        adapter_name = _lang_to_adapter_name(pl.language)
        if adapter_name is None:
            logger.warning(f"No engine adapter for language: {pl.language}. Skipping.")
            continue
        if adapter_name not in names:
            names.append(adapter_name)
    # TypeScript's adapter already covers the JavaScript suffixes, so a second engine over
    # the same family would only duplicate it. JavaScript keeps its own when alone.
    if AdapterName.TYPESCRIPT in names and AdapterName.JAVASCRIPT in names:
        names.remove(AdapterName.JAVASCRIPT)
    return names


def _csharp_solution_members(csharp_projects: list[CSharpProjectConfig]) -> dict[Path, list[Path]]:
    """Per C# root, the project files its solution files list."""
    members: dict[Path, list[Path]] = {}
    for config in csharp_projects:
        solutions = [path for pattern in SOLUTION_GLOBS for path in config.root.glob(pattern)]
        members[config.root] = [project for solution in solutions for project in solution_projects(solution)]
    return members


def _create_engine_configs(
    programming_languages: list[ProgrammingLanguage],
    repository_path: Path,
    ignore_manager: RepoIgnoreManager,
) -> list[EngineConfig]:
    """Create one ``EngineConfig`` per sub-project from the detected languages.

    Handles monorepo support: for TypeScript/Java/C#, scans for multiple
    project configurations and emits one entry per sub-project.
    """
    configs: list[EngineConfig] = []

    for adapter_name in _adapter_names_for(programming_languages):
        try:
            adapter = get_adapter(adapter_name)
        except ValueError:
            logger.warning(f"Engine adapter not found for: {adapter_name}. Skipping.")
            continue

        try:
            if adapter_name in (AdapterName.TYPESCRIPT, AdapterName.JAVASCRIPT):
                ts_config_scanner = TypeScriptConfigScanner(repository_path, ignore_manager=ignore_manager)
                typescript_projects = ts_config_scanner.find_typescript_projects()

                if typescript_projects:
                    # One LSP rooted at the repo, fed the union of all
                    # leaf-tsconfig files. Why: tsserver attaches each
                    # ``didOpen`` file to its nearest enclosing tsconfig
                    # (Configured Project), and one server sees both ends
                    # of every cross-project call. A large monorepo's full
                    # pass runs one server per project instead, as one
                    # holding every project runs out of memory; the merge
                    # links the calls between them.
                    union: list[Path] = []
                    seen: set[Path] = set()
                    for project in typescript_projects:
                        for f in project.files:
                            if f not in seen:
                                seen.add(f)
                                union.append(f)
                    project_dirs = ", ".join(str(p.root.relative_to(repository_path)) for p in typescript_projects)
                    logger.info(
                        f"Creating engine config for {adapter_name} at repo root "
                        f"({len(union)} files across {len(typescript_projects)} tsconfig project(s): "
                        f"{project_dirs})"
                    )
                    # tsconfig membership is authoritative for what it covers, but it omits
                    # .js unless ``allowJs`` is set. One adapter owns the whole family, so top
                    # up with what no project claimed rather than analysing half a mixed repo.
                    unclaimed = ts_config_scanner.find_unclaimed_family_files(typescript_projects)
                    if unclaimed:
                        logger.info(f"Adding {len(unclaimed)} family file(s) claimed by no tsconfig")
                        union.extend(unclaimed)
                    split: list[EngineConfig] = []
                    if len(typescript_projects) > 1 and len(union) >= TYPESCRIPT_SPLIT_MIN_FILES:
                        # Largest first, so a project too large for one server fails the run before the rest ran.
                        for project in sorted(typescript_projects, key=lambda p: (-len(p.files), str(p.root))):
                            split.append(EngineConfig(adapter, repository_path, source_files=list(project.files)))
                        if unclaimed:
                            split.append(EngineConfig(adapter, repository_path, source_files=unclaimed))
                        logger.info(f"The {adapter_name} full pass runs {len(split)} servers in turn")
                    configs.append(EngineConfig(adapter, repository_path, source_files=union, split=split))
                else:
                    logger.info(f"No TypeScript config files found, using repository root for {adapter_name}")
                    configs.append(EngineConfig(adapter, repository_path))

            elif adapter_name == AdapterName.JAVA:
                java_config_scanner = JavaConfigScanner(repository_path, ignore_manager=ignore_manager)
                java_projects = java_config_scanner.scan()

                if java_projects:
                    for project_config in java_projects:
                        logger.info(
                            f"Creating engine config for Java ({project_config.build_system}) at: "
                            f"{project_config.root.relative_to(repository_path)}"
                        )
                        configs.append(EngineConfig(adapter, project_config.root))
                else:
                    logger.info("No Java projects detected")

            elif adapter_name == AdapterName.CSHARP:
                csharp_scanner = CSharpConfigScanner(repository_path, ignore_manager=ignore_manager)
                csharp_projects = csharp_scanner.scan()

                if csharp_projects:
                    members = _csharp_solution_members(csharp_projects)
                    for csharp_config in csharp_projects:
                        logger.info(
                            f"Creating engine config for CSharp ({csharp_config.project_type}) at: "
                            f"{csharp_config.root.relative_to(repository_path)}"
                        )
                        # A project a nested root's solution lists, and this root's solutions do not,
                        # is that engine's; naming it here as well would put it in the graph twice.
                        elsewhere = [
                            project.parent
                            for root, projects in members.items()
                            if root != csharp_config.root and root.is_relative_to(csharp_config.root)
                            for project in projects
                            if project not in members[csharp_config.root]
                        ]
                        source_files = adapter.discover_source_files(csharp_config.root, ignore_manager, elsewhere)
                        if not source_files:
                            logger.info(
                                f"Every C# file under {csharp_config.root} belongs to a nested solution; skipping"
                            )
                            continue
                        configs.append(
                            EngineConfig(
                                adapter, csharp_config.root, source_files=source_files, excluded_roots=elsewhere
                            )
                        )
                else:
                    logger.info("No C# projects detected")

            else:
                configs.append(EngineConfig(adapter, repository_path))

        except RuntimeError as e:
            logger.error(f"Failed to create engine config for {adapter_name}: {e}")

    return configs


def _lang_to_adapter_name(language: str) -> str | None:
    """Map a ProgrammingLanguage name to the engine adapter registry key."""
    mapping: dict[str, str] = {
        Language.PYTHON: AdapterName.PYTHON,
        Language.TYPESCRIPT: AdapterName.TYPESCRIPT,
        Language.JAVASCRIPT: AdapterName.JAVASCRIPT,
        Language.CSHARP: AdapterName.CSHARP,
        Language.GO: AdapterName.GO,
        Language.JAVA: AdapterName.JAVA,
        Language.KOTLIN: AdapterName.KOTLIN,
        Language.PHP: AdapterName.PHP,
        Language.RUST: AdapterName.RUST,
        # Scanner spellings with no ``Language`` member of their own.
        "tsx": AdapterName.TYPESCRIPT,
        "jsx": AdapterName.JAVASCRIPT,
        "c#": AdapterName.CSHARP,
    }
    return mapping.get(language.lower())


class StaticAnalyzer:
    """Sole responsibility: Analyze the code using the engine LSP pipeline.

    Why ``resident_servers=False``: a one-shot run keeps one server up at a time, so its peak is one server's memory.
    """

    def __init__(self, repository_path: Path, changed_files: set[Path] | None = None, resident_servers: bool = True):
        self.repository_path = repository_path.resolve()
        # How many servers an analysis runs at once, each for its own engine; 0 keeps one per config resident.
        self._server_slots = max_concurrent_engines() or (0 if resident_servers else 1)
        self.ignore_manager = RepoIgnoreManager(self.repository_path)
        self.programming_langs = ProjectScanner(self.repository_path).scan()
        self._engine_configs = _create_engine_configs(self.programming_langs, self.repository_path, self.ignore_manager)
        self._engine_clients: list[tuple[EngineConfig, LSPClient]] = []
        # (language, project) -> the failure preparation raised, or None.
        self._prepared_projects: dict[tuple[str, Path], Exception | None] = {}
        self._prepared_lock = threading.Lock()
        self.collected_diagnostics: dict[Language, FileDiagnosticsMap] = {}
        self._clients_started: bool = False
        self._cached_results: StaticAnalysisResults | None = None
        # Git-free changed-file set (absolute paths) scoping the warm-start re-LSP,
        # e.g. the incremental fingerprint diff. ``None`` means "detect via git"
        # (the legacy CLI-on-a-real-checkout path); an empty set re-LSPs nothing.
        self.changed_files = changed_files
        # Resolved here so an unusable value fails before any engine starts, where
        # the per-language handlers would turn it into "skip this language".
        self._request_timeout_override = lsp_request_timeout_override()
        if self._request_timeout_override is not None:
            logger.info("Per-request LSP timeout overridden to %ds", self._request_timeout_override)

    def __enter__(self) -> "StaticAnalyzer":
        self.start_clients()
        return self

    def __exit__(self, _exc_type: type | None, _exc_val: Exception | None, _exc_tb: object | None) -> None:
        self.stop_clients()

    def start_clients(self) -> None:
        """Start all engine LSP server processes.

        Call once before invoking analyze() or analyze_with_cluster_changes().
        Idempotent — safe to call even if clients are already running.

        A failing client is skipped and logged; ``RuntimeError`` is raised
        only when every configured client fails.
        """
        if self._clients_started:
            logger.info(f"Clients already started for {self.repository_path}, skipping start.")
            return

        if not self._engine_configs:
            logger.info(f"No supported languages detected in {self.repository_path}; no LSP clients to start.")
            self._engine_clients = []
            self._clients_started = True
            return

        started: list[tuple[EngineConfig, LSPClient]] = []
        attempted: list[str] = []
        failed_languages: list[str] = []
        failed_details: list[str] = []

        for engine_config in self._engine_configs:
            adapter, project_path = engine_config.adapter, engine_config.project_path
            # Resolve file membership before paying for the server. A language
            # the scanner detected from a handful of stray files still costs a
            # full LSP process (tsserver + its node workers run ~350MB) that
            # then analyzes nothing. Also reused by ``_run_full_analysis``, so
            # the directory walk happens once per engine rather than twice.
            if not engine_config.source_files:
                engine_config.source_files = adapter.discover_source_files(project_path, self.ignore_manager)
            if not engine_config.source_files:
                logger.info(f"No {adapter.language} source files under {project_path}; skipping its LSP server.")
                continue

            attempted.append(adapter.language)
            if self._server_slots:
                # Each analysis starts the server it needs and stops it when done.
                continue
            try:
                started.append((engine_config, self._spawn_engine_client(engine_config)))
            except StaticAnalysisFatalError:
                for _, client in started:  # never handed to ``stop_clients``
                    client.shutdown()
                raise
            except Exception as exc:
                logger.exception(
                    f"Failed to start engine LSP client for {adapter.language}; "
                    f"skipping this language and continuing"
                )
                failed_languages.append(adapter.language)
                failed_details.append(f"{adapter.language}: {exc}")

        if not attempted:
            logger.info(f"No source files for any detected language in {self.repository_path}; no LSP clients started.")
            self._engine_clients = []
            self._clients_started = True
            return

        if self._server_slots:
            logger.info(
                "%d engine LSP client(s) will start during analysis, at most %d running at a time.",
                len(attempted),
                self._server_slots,
            )
            # Prepare everything now, while nothing is resident: a project must
            # not be analyzed before its siblings are restored.
            for engine_config in self._engine_configs:
                if not engine_config.source_files:
                    continue
                try:
                    self._prepare_project_once(engine_config)
                except Exception:
                    logger.exception(f"Failed to prepare {engine_config.project_path}; the full pass will report it")
            self._engine_clients = []
            self._clients_started = True
            return

        if not started:
            self._clients_started = False
            details = f"; failures: {'; '.join(failed_details)}" if failed_details else ""
            raise RuntimeError(f"Failed to start any engine LSP client (attempted: {', '.join(attempted)}){details}")

        if failed_languages:
            details = f" Details: {'; '.join(failed_details)}." if failed_details else ""
            logger.warning(
                f"Proceeding with partial LSP coverage. "
                f"Failed: {', '.join(failed_languages)}. "
                f"Started: {', '.join(s.adapter.language for s, _ in started)}."
                f"{details}"
            )

        self._engine_clients = started
        self._clients_started = True

    def _prepare_project_once(self, engine_config: EngineConfig) -> None:
        """Let the adapter prepare a project exactly once (e.g. ``dotnet restore``).

        Why once, and why up front under a concurrency cap: restore writes the
        ``obj/`` artifacts a sibling project's compilation resolves against, so
        an engine that runs before its siblings are restored loses cross-project
        edges. Preparing every project before the first server starts keeps the
        bounded pass resolving exactly what the unbounded one does.

        A failure is remembered and re-raised rather than retried: preparation
        installs SDKs into a shared directory, so several engines retrying it
        concurrently is worse than one engine reporting it.
        """
        key = (engine_config.adapter.language, engine_config.project_path)
        with self._prepared_lock:
            if key in self._prepared_projects:
                failure = self._prepared_projects[key]
                if failure is not None:
                    raise failure
                return
        outcome: Exception | None = None
        try:
            engine_config.adapter.prepare_project(engine_config.project_path)
        except Exception as exc:
            outcome = exc
            raise
        finally:
            with self._prepared_lock:
                self._prepared_projects[key] = outcome

    def _spawn_engine_client(self, engine_config: EngineConfig) -> LSPClient:
        """Start one engine's LSP server and return it ready for queries.

        The server is started on the workspace its adapter names (see ``workspace_folders``), and
        one that could not load the project is restarted once on the workspace its adapter
        describes instead (see ``fallback_workspace_folders``).
        """
        adapter, project_path = engine_config.adapter, engine_config.project_path
        logger.info(f"Starting engine LSP client for {adapter.language} at {project_path}")
        self._prepare_project_once(engine_config)
        initial = adapter.workspace_folders(project_path, engine_config.source_files)
        engine_client = self._start_engine_client(engine_config, initial or None)
        try:
            fallback = adapter.fallback_workspace_folders(engine_client, project_path, engine_config.source_files)
        except Exception:
            engine_client.shutdown()
            raise
        if not fallback or fallback == initial:
            return engine_client
        logger.warning(f"{adapter.language} server could not load {project_path}; restarting on {fallback}")
        engine_client.shutdown()
        return self._start_engine_client(engine_config, fallback)

    def _replace_client(self, engine_config: EngineConfig, engine_client: LSPClient) -> LSPClient:
        """Shut one engine's server down and swap a freshly spawned one into its place."""
        logger.warning(f"Replacing the {engine_config.adapter.language} server for {engine_config.project_path}")
        engine_client.shutdown()
        fresh = self._spawn_engine_client(engine_config)
        self._engine_clients = [(cfg, fresh if c is engine_client else c) for cfg, c in self._engine_clients]
        return fresh

    def _start_engine_client(
        self, engine_config: EngineConfig, workspace_folders: list[Path] | None = None
    ) -> LSPClient:
        """Launch the server, initialize it, and wait for its workspace where the adapter asks to."""
        adapter, project_path = engine_config.adapter, engine_config.project_path
        t_start = time.monotonic()
        command = adapter.get_lsp_command(project_path)
        # Servers run N at a time share the memory budget; one at a time, or resident, each may use all of it.
        heap = per_engine_memory_budget(self._server_slots) - SERVER_NON_HEAP_BYTES
        init_options = {**adapter.get_lsp_init_options(self.ignore_manager), **adapter.get_lsp_heap_options(heap)}
        extra_env = adapter.get_lsp_env(project_path, engine_config.source_files)
        # Node-based LSPs spawn child ``node`` processes by name; on
        # a Node-less host the embedded runtime's dir must be on PATH.
        ensure_node_on_path(command, extra_env)
        engine_client = LSPClient(
            command=command,
            project_root=project_path,
            init_options=init_options,
            default_timeout=self._request_timeout_override or adapter.get_lsp_default_timeout(),
            startup_timeout=adapter.get_lsp_startup_timeout(),
            collect_diagnostics=True,
            extra_env=extra_env,
            workspace_settings=adapter.get_workspace_settings(),
            extra_client_capabilities=getattr(adapter, "extra_client_capabilities", {}) or {},
            error_verdict=adapter.error_verdict,
            workspace_folders=workspace_folders,
            commands=adapter.get_lsp_commands(project_path),
        )
        try:
            engine_client.start()
            t_lsp_started = time.monotonic()
            logger.info(f"{adapter.language} LSP start: {t_lsp_started - t_start:.1f}s")

            # Some LSP servers (JDTLS, rust-analyzer) load workspace metadata
            # asynchronously and only respond to cross-file queries once that's
            # complete. Adapters opt in via ``wait_for_workspace_ready`` so the
            # language-name check doesn't keep growing.
            if adapter.wait_for_workspace_ready:
                engine_client.wait_for_server_ready()
                adapter.validate_workspace_ready(engine_client)
                logger.info(f"{adapter.language} workspace ready: {time.monotonic() - t_lsp_started:.1f}s")
        except Exception:
            try:
                engine_client.shutdown()
            except Exception:
                logger.exception(f"Error shutting down partially-started {adapter.language} client during cleanup")
            raise
        return engine_client

    def stop_clients(self) -> None:
        """Gracefully shut down all engine LSP server processes. Idempotent."""
        if not self._clients_started:
            return
        for engine_config, client in self._engine_clients:
            try:
                client.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down engine LSP client for {engine_config.adapter.language}: {e}")
        self._engine_clients = []
        self._clients_started = False
        self._cached_results = None

    def _live_clients(self, operation: str) -> list[tuple[EngineConfig, LSPClient]]:
        """The started engine clients, refusing callers when each server lives only for its own analysis.

        Why: every caller iterates the client list, and an empty one would read as a successful empty answer.
        """
        if self._engine_clients or not any(config.source_files for config in self._engine_configs):
            return self._engine_clients
        raise StaticAnalysisFatalError(
            f"{operation} needs resident LSP servers, but this analyzer starts each one only for its own analysis. "
            f"Construct it with resident_servers=True and leave {MAX_CONCURRENT_ENGINES_ENV_VAR} unset."
        )

    def collect_fresh_diagnostics(self) -> dict[Language, FileDiagnosticsMap]:
        """Read current diagnostics from all running LSP clients without re-analyzing.

        The LSP servers accumulate ``textDocument/publishDiagnostics`` notifications
        automatically after ``didChange``.  This method reads the collected
        diagnostics without triggering any new analysis work.
        """
        result: dict[Language, FileDiagnosticsMap] = {}
        for engine_config, client in self._live_clients("collect_fresh_diagnostics"):
            diags = client.get_collected_diagnostics()
            if diags:
                result[engine_config.adapter.results_language] = diags
        return result

    def get_diagnostics_generation(self) -> int:
        """Return the sum of diagnostics generation counters across all LSP clients."""
        return sum(
            client.get_diagnostics_generation() for _, client in self._live_clients("get_diagnostics_generation")
        )

    def load_cached_analysis(
        self,
        artifact_dir: Path | None = None,
        expected_sha: str | None = None,
    ) -> StaticAnalysisResults | None:
        """Rehydrate the on-disk run artifact for read-only reuse, or None if absent/stale.

        Used by health/status consumers that reuse the last analysis's call
        graph without re-analyzing.

        Args:
            artifact_dir: Optional artifact directory to load from. If None,
                uses ``<repository_path>/.codeboarding/`` (sibling of
                ``analysis.json``).
            expected_sha: When provided, only return cached results whose
                tag-file SHA matches; otherwise treated as a cache miss
                without unpickling. Stops stale-cache hits when the source
                has drifted between the save and the load.

        Returns:
            Cached StaticAnalysisResults if found and SHA-validated (or no
            SHA gate was requested), None otherwise.  Sets
            ``_cached_results`` so subsequent calls are free.
        """
        if self._cached_results is not None:
            return self._cached_results

        load_dir = Path(artifact_dir) if artifact_dir is not None else get_artifact_dir(self.repository_path)
        static_analysis_cache = StaticAnalysisCache(load_dir, self.repository_path)
        cached_results = static_analysis_cache.get(expected_sha=expected_sha)
        if cached_results is not None:
            self._cached_results = cached_results
            self.collected_diagnostics = cached_results.diagnostics
        return cached_results

    def notify_file_changed(self, file_path: Path, content: str) -> None:
        """Notify the LSP server that the editor has saved new content for a file.

        Sends textDocument/didOpen with the new content to the appropriate
        engine LSP client based on file extension.

        Args:
            file_path: Absolute path to the changed file.
            content:   Full current text content of the file.
        """
        suffix = file_path.suffix
        for engine_config, client in self._live_clients("notify_file_changed"):
            adapter = engine_config.adapter
            if suffix in adapter.file_extensions:
                # Open + change to ensure the server has the latest content
                client.did_open(file_path)
                client.did_change(file_path, content)
                logger.debug(f"Sent didOpen+didChange for {file_path} to {adapter.language} engine LSP")

    def get_file_symbols(self, file_path: Path) -> list[dict]:
        """Query the LSP server for document symbols in a single file.

        Args:
            file_path: Absolute path to the file.

        Returns:
            Raw LSP ``DocumentSymbol[]`` response (possibly nested).
            Returns an empty list if no matching client is found.
        """
        suffix = file_path.suffix
        for engine_config, client in self._live_clients("get_file_symbols"):
            if suffix in engine_config.adapter.file_extensions:
                # Why: a replaced server starts with nothing open, and tsls answers an unopened document with [].
                if not engine_config.adapter.workspace_owns_documents:
                    client.did_open(file_path)
                symbols = client.document_symbol(file_path)
                logger.debug(f"Got {len(symbols)} symbols for {file_path}")
                return symbols
        return []

    def get_adapter_for_file(self, file_path: Path) -> tuple[LanguageAdapter, Path] | None:
        """Return the (adapter, project_root) pair that handles a given file extension."""
        suffix = file_path.suffix
        for engine_config, _ in self._live_clients("get_adapter_for_file"):
            if suffix in engine_config.adapter.file_extensions:
                return engine_config.adapter, engine_config.project_path
        return None

    def discover_file_dependencies(self, file_path: Path) -> list[str]:
        """Discover files that a source file depends on via call-site resolution.

        Uses ``SourceInspector`` to find call sites in the file, then resolves
        each call site to its definition location using the LSP server.

        Args:
            file_path: Absolute path to the source file.

        Returns:
            Deduplicated list of absolute file paths that the file depends on.
            Returns an empty list if no client serves the file; a failed query raises.
        """
        suffix = file_path.suffix
        engine = next(
            (
                (engine_config, client)
                for engine_config, client in self._live_clients("discover_file_dependencies")
                if suffix in engine_config.adapter.file_extensions
            ),
            None,
        )
        if engine is None:
            return []
        engine_config, client = engine

        call_sites = SourceInspector().find_call_sites(file_path)
        if not call_sites:
            return []

        queries = [(file_path, site.lsp_line, site.lsp_column) for site in call_sites]
        if not engine_config.adapter.workspace_owns_documents:
            client.did_open(file_path)
        results = client.send_definition_batch(queries)

        resolved = file_path.resolve()
        unique_paths: set[str] = set()
        for definitions in results:
            for defn in definitions:
                uri = defn.get("targetUri", defn.get("uri", ""))
                if not uri.startswith("file://"):
                    continue
                dep_path_obj = uri_to_path(uri)
                if dep_path_obj is None:
                    continue
                dep_path = str(dep_path_obj)
                if dep_path != str(resolved):
                    unique_paths.add(dep_path)

        logger.debug(f"Discovered {len(unique_paths)} dependencies for {file_path}")
        return list(unique_paths)

    def analyze(
        self,
        cache_dir: Path,
        skip_cache: bool = False,
        source_sha: str | None = None,
    ) -> StaticAnalysisResults:
        """Analyze the repository, warm-starting from the SHA-tagged pkl when present.

        Flow:

        1. In-memory cache hit -> return.
        2. ``skip_cache=True`` -> full LSP analysis.
        3. Pkl present -> load it, scope the warm-start to ``self.changed_files``
           (or git when that is ``None``), re-LSP just those, merge in memory.
        4. No pkl -> full LSP.

        Clients must be running before calling this method. Use ``start_clients()``
        or the context manager (``with StaticAnalyzer(...) as sa:``).
        """
        if not self._clients_started:
            raise RuntimeError(
                "LSP clients are not running. Call start_clients() or use StaticAnalyzer as a context manager "
                "('with StaticAnalyzer(...) as sa:') before calling analyze()."
            )

        if not skip_cache and self._cached_results is not None:
            logger.info("static_analysis_cache: outcome=memhit")
            return self._cached_results

        logger.info(f"analyze() called with skip_cache={skip_cache}, source_sha={'<set>' if source_sha else None}")

        cache = StaticAnalysisCache(cache_dir, self.repository_path)

        if skip_cache:
            logger.info("static_analysis_cache: outcome=bypass (skip_cache=True)")
            results = self._run_full_lsp_pass()
        else:
            warm_start = cache.load_with_sha()
            if warm_start is None:
                # An artifact that is present but unreadable means an engine version whose
                # graph this build would not reproduce. Refuse rather than quietly running a
                # full pass: the caller asked for incremental, and the full result would
                # overwrite the very artifact a later run could have reused.
                if self.changed_files is not None and cache.pkl_path.exists() and cache.sha_path.exists():
                    raise StaticAnalysisFatalError(
                        f"{cache.pkl_path} was written by a different engine version and cannot be "
                        "reused for an incremental run. Re-run a full analysis to rebuild it."
                    )
                logger.info("static_analysis_cache: outcome=miss_absent")
                results = self._run_full_lsp_pass()
            else:
                cached_results, cached_sha = warm_start
                logger.info(
                    "static_analysis_cache: outcome=warmstart (cached_sha=%s, current_sha=%s, changes=%s)",
                    cached_sha,
                    source_sha or "<none>",
                    "supplied" if self.changed_files is not None else "git",
                )
                results = self._update_cached_results(cached_results, cached_sha)

        self._validate_analysis_results(results)
        results.diagnostics = self.collected_diagnostics
        self._cached_results = results
        return results

    def _run_full_lsp_pass(self) -> StaticAnalysisResults:
        """Run a fresh LSP analysis for every started engine client.

        Cold path: nothing reusable on disk, so every language re-indexes.
        ``analyze()`` calls this only when the pkl is missing or the caller
        explicitly requested ``skip_cache=True``.
        """
        results = StaticAnalysisResults()

        def analyse_or_leave_out(
            engine_config: EngineConfig, client: LSPClient | None
        ) -> list[tuple[LanguageAdapter, dict]]:
            """A config that fails short of a fatal error is logged and left out, so the others still report."""
            t_start = time.monotonic()
            try:
                return self._analyse_config(engine_config, client)
            except StaticAnalysisFatalError:
                raise
            except Exception:
                adapter = engine_config.adapter
                logger.exception(f"Error during engine analysis for {adapter.language} in {engine_config.project_path}")
                track_lsp_result(
                    language=adapter.language_enum.value,
                    loc=self._loc_for_adapter(adapter),
                    status="error",
                    duration_ms=round((time.monotonic() - t_start) * 1000),
                    analysis={},
                    diagnostics={},
                )
                return []

        cap = self._server_slots
        # Each config with its resident server, or with None for ``_analyse_config`` to start one.
        engines = [(cfg, None) for cfg in self._engine_configs if cfg.source_files] if cap else self._engine_clients
        if cap <= 1:
            # On the calling thread, so Ctrl-C stops the run instead of waiting out a pool thread.
            per_engine = [analyse_or_leave_out(engine_config, client) for engine_config, client in engines]
        else:
            logger.info("Running %d engine(s) with at most %d resident at a time", len(engines), cap)
            # Not ``with``: its ``__exit__`` waits without cancelling, so one
            # engine's fatal error would still drain every queued engine — a full
            # pass over a monorepo — before propagating. Cancel the queue, then
            # wait so each in-flight analysis still shuts its own client down.
            pool = ThreadPoolExecutor(max_workers=cap)
            try:
                futures = [pool.submit(analyse_or_leave_out, engine_config, None) for engine_config, _ in engines]
                for future in as_completed(futures):
                    future.result()
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
            per_engine = [future.result() for future in futures]
        # In configuration order, not completion order: node and reference merges keep the first engine's entry
        # for a key two engines report (overlapping configs such as nested solution roots do), so completion
        # order would let two identical runs keep different nodes and produce different component IDs.
        self._absorb_and_link(results, [analysis for analyses in per_engine for analysis in analyses])

        summaries = []
        for language in results.get_languages():
            try:
                cfg = results.get_cfg(language)
                node_count = len(cfg.nodes)
                edge_count = len(cfg.edges)
            except ValueError:
                node_count = 0
                edge_count = 0
            summaries.append(
                f"{language.value}: {len(results.get_source_files(language))} files, "
                f"{sum(1 for _ in results.iter_reference_nodes(language))} references, "
                f"{node_count} nodes, {edge_count} edges"
            )
        logger.info("Static analysis complete: %s", "; ".join(summaries) or "no languages")
        return results

    def _analyse_config(
        self, engine_config: EngineConfig, resident_client: LSPClient | None
    ) -> list[tuple[LanguageAdapter, dict]]:
        """Analyse one config fully, on its resident server or on one started for it and stopped after.

        A split config runs its parts in turn, each on a server of its own, with its resident server down meanwhile:
        one server at a time holds the memory budget. Any part failing fails the config.
        """
        adapter, project_path = engine_config.adapter, engine_config.project_path
        t_start = time.monotonic()
        if resident_client is not None and engine_config.split:
            resident_client.shutdown()  # replaced once the parts are done
        elif resident_client is not None and not resident_client.alive:
            # Why: an editor keeps its analyzer, so a server that died in an earlier run would fail every later one.
            resident_client = self._replace_client(engine_config, resident_client)
        server = None if engine_config.split else resident_client
        analyses: list[dict] = []
        try:
            for part in engine_config.split or [engine_config]:
                engine_client = server or self._spawn_engine_client(part)
                try:
                    logger.info(f"Starting engine analysis for {adapter.language} in {project_path}")
                    analyses.append(self._run_full_analysis(part, engine_client))
                    self._collect_diagnostics_for(adapter, engine_client, analyses[-1])
                finally:
                    if server is None:
                        engine_client.shutdown()
        finally:
            if resident_client is not None and engine_config.split:
                self._replace_client(engine_config, resident_client)
        logger.info(f"Engine analysis for {adapter.language} completed in {time.monotonic() - t_start:.1f}s")
        for analysis in analyses:
            track_lsp_result(
                language=adapter.language_enum.value,
                loc=self._loc_for_adapter(adapter),
                status="success",
                duration_ms=round((time.monotonic() - t_start) * 1000),
                analysis=analysis,
                diagnostics=self.collected_diagnostics.get(adapter.results_language, {}),
            )
        return [(adapter, analysis) for analysis in analyses]

    def _update_cached_results(
        self,
        cached_results: StaticAnalysisResults,
        cached_sha: str,
    ) -> StaticAnalysisResults:
        """Bring *cached_results* up to date in-memory, scoped to the changed files.

        Per language: determine the changed-file list, hand it to
        ``update_cfg_for_changed_files`` along with the language's portion of the
        cached state, and put the merged result back into a fresh
        ``StaticAnalysisResults``.

        Changed-file source: ``self.changed_files`` when set at construction
        (git-free — e.g. the wrapper's fingerprint diff), else ``git diff`` via
        ``get_changed_files_since``. If git fails (*cached_sha* unreachable, a
        non-git frozen copy, or a content-hash SHA that isn't a git object), fall
        back to a full re-LSP for that language so the run still produces valid
        output.
        """
        results = StaticAnalysisResults()
        # One dict per language, threaded through every engine config of that language and
        # absorbed once. Why: each config re-LSPs only the changed files under its own root,
        # so a config that took its own copy of the cache would hand back the nodes another
        # config had just invalidated, and the merge would resurrect deleted files.
        carried: dict[Language, dict] = {}
        carried_adapters: dict[Language, LanguageAdapter] = {}
        rebuilt: list[tuple[LanguageAdapter, dict]] = []
        engines = (
            [(config, None) for config in self._engine_configs if config.source_files]
            if self._server_slots
            else self._live_clients("warm-start")
        )
        for engine_config, resident_client in engines:
            adapter, project_path = engine_config.adapter, engine_config.project_path
            language = adapter.results_language
            changed_files = self._changed_files_for_language(project_path, cached_sha, adapter.language)
            if changed_files is not None and adapter.rebuilds_for(changed_files, project_path):
                logger.info(f"warmstart {adapter.language}: a change alters how imports resolve; rebuilding")
                changed_files = None
                if resident_client is not None:
                    # Why: a server resolves imports as the project was when it started; a stopped one is replaced.
                    resident_client.shutdown()
            # A full re-analysis is the full pass's, so a split config runs as its parts. Changed files stay on the
            # config's own server: tsserver loads only the projects of the files it opens.
            if changed_files is None:
                rebuilt += self._analyse_config(engine_config, resident_client)
                continue
            t_lang_start = time.monotonic()
            if resident_client is not None and not resident_client.alive:  # see ``_analyse_config``
                resident_client = self._replace_client(engine_config, resident_client)
            try:
                # A one-shot run starts each server for its own step and stops it after.
                engine_client = resident_client or self._spawn_engine_client(engine_config)
            except StaticAnalysisFatalError:
                raise
            except Exception:
                # Same rule as the full pass: a language whose server cannot start is left out.
                logger.exception(f"Could not start the {adapter.language} server for {project_path}; skipping it")
                continue
            try:
                changed_files = {
                    path
                    for path in changed_files
                    if not any(path.is_relative_to(root) for root in engine_config.excluded_roots)
                }
                logger.info(f"warmstart {adapter.language}: re-LSPing {len(changed_files)} changed file(s)")
                cached_lang_dict = carried.get(language) or self._extract_language_dict(cached_results, language)
                analysis = update_cfg_for_changed_files(
                    cached_lang_dict,
                    changed_files,
                    adapter,
                    project_path,
                    self.repository_path,
                    engine_client,
                    self.ignore_manager,
                )
                engine_client.check_alive()
                carried[language] = analysis
                carried_adapters[language] = adapter
                self._collect_diagnostics_for(adapter, engine_client, analysis)
            finally:
                if resident_client is None:
                    engine_client.shutdown()
            track_lsp_result(
                language=adapter.language_enum.value,
                loc=self._loc_for_adapter(adapter),
                status="success",
                duration_ms=round((time.monotonic() - t_lang_start) * 1000),
                analysis=analysis,
                diagnostics=self.collected_diagnostics.get(adapter.results_language, {}),
            )
        self._absorb_and_link(
            results, rebuilt + [(carried_adapters[language], analysis) for language, analysis in carried.items()]
        )
        results.incremental_base_results = cached_results
        return results

    def _changed_files_for_language(self, project_path: Path, cached_sha: str, language: str) -> set[Path] | None:
        """The warm-start changed-file set scoped to one language's project root.

        ``self.changed_files`` when set (git-free), else ``git diff`` via
        ``get_changed_files_since``. ``None`` means "detect failed / no set" and
        the caller does a full re-LSP for the language.
        """
        if self.changed_files is not None:
            # Scope the repo-wide set to this language's project root so a
            # multi-language repo doesn't re-LSP every changed file per engine.
            return {f for f in self.changed_files if f.is_relative_to(project_path)}
        try:
            return set(get_changed_files_since(project_path, cached_sha))
        except Exception as e:
            logger.warning(
                f"get_changed_files_since failed for {language} (cached_sha={cached_sha}): {e}; "
                "falling back to full re-LSP for this language"
            )
            return None

    def _extract_language_dict(self, cached_results: StaticAnalysisResults, language: Language) -> dict:
        """Project a single language's bucket out of ``StaticAnalysisResults`` into the dict shape ``update_cfg_for_changed_files`` expects."""
        try:
            cached_cfg = cached_results.get_cfg(language)
        except ValueError:
            cached_cfg = CallGraph(language=language)
        try:
            class_hierarchies = cached_results.get_hierarchy(language)
        except ValueError:
            class_hierarchies = {}
        try:
            package_relations = cached_results.get_package_dependencies(language)
        except ValueError:
            package_relations = {}
        cached_refs = list(cached_results.iter_reference_nodes(language))
        cached_source_files = [Path(p) for p in cached_results.get_source_files(language)]
        return {
            "call_graph": cached_cfg,
            "class_hierarchies": class_hierarchies,
            "package_relations": package_relations,
            "references": cached_refs,
            "source_files": cached_source_files,
            "diagnostics": cached_results.diagnostics.get(language, {}),
        }

    def _absorb_and_link(self, results: StaticAnalysisResults, analyses: list[tuple[LanguageAdapter, dict]]) -> None:
        """Absorb every engine's analysis, then finish the calls whose definitions lie in another engine's files.

        Why after all of them: only the merged graph holds every engine's nodes, so a call into
        another solution's project can find its target, whichever engine ran first.
        """
        for adapter, analysis in analyses:
            self._absorb_into_results(results, adapter.results_language, analysis)
        for language in results.get_languages():
            link_declared_bases(results.get_hierarchy(language), results.get_cfg(language))
        inspector = SourceInspector()
        for adapter, analysis in analyses:
            sites: list[ExternalCallSite] = analysis.get("external_call_sites", [])
            if not sites:
                continue
            language = adapter.results_language
            analysed_files = {str(path) for path in analysis.get("source_files", [])}
            # Why the references and callers too: an engine keeps a constant or a local only as an edge's end.
            endpoints = [*results.iter_reference_nodes(language), *analysis.get("external_callers", [])]
            index = GraphIndex(results.get_cfg(language), endpoints)
            linked = link_external_call_sites(index, sites, adapter, inspector, analysed_files)
            record_package_imports(results.get_package_dependencies(language), adapter, linked.edges)

    def _absorb_into_results(self, results: StaticAnalysisResults, language: Language, analysis: dict) -> None:
        """Stuff one language's analysis-dict into the shared ``StaticAnalysisResults``."""
        results.add_references(language, analysis.get("references", []))
        call_graph = analysis.get("call_graph") or CallGraph()
        results.add_cfg(language, call_graph)
        results.add_class_hierarchy(language, analysis.get("class_hierarchies", {}))
        results.add_package_dependencies(language, analysis.get("package_relations", {}))
        results.add_source_files(language, [str(f) for f in analysis.get("source_files", [])])

    def _collect_diagnostics_for(self, adapter: LanguageAdapter, engine_client: LSPClient, analysis: dict) -> None:
        """Merge cached + live diagnostics for one adapter into ``self.collected_diagnostics``.

        Why: rust-analyzer / csharp-ls publish diagnostics asynchronously
        after ``didOpen``; ``adapter.wait_for_diagnostics`` is the
        per-adapter quiescence signal that prevents us from snapshotting an
        empty ``collected_diagnostics`` map.
        """
        cache_diags: dict = analysis.get("diagnostics") or {}
        t_wait = time.monotonic()
        adapter.wait_for_diagnostics(engine_client)
        logger.debug(f"wait_for_diagnostics for {adapter.language}: {time.monotonic() - t_wait:.1f}s")
        live_diags = engine_client.get_collected_diagnostics()
        merged_diags: dict = dict(cache_diags)
        for fp, diags in live_diags.items():
            merged_diags[fp] = diags
        if merged_diags:
            total = sum(len(d) for d in merged_diags.values())
            logger.info(
                f"Diagnostics for {adapter.language}: {len(merged_diags)} files, {total} items "
                f"(cache={len(cache_diags)}, live={len(live_diags)})"
            )
        # Merge, not replace: a monorepo yields several configs per language, and
        # replacing kept only whichever finished last -- which under the concurrency
        # bound is a different one run to run.
        self.collected_diagnostics.setdefault(adapter.results_language, {}).update(merged_diags)

    def _loc_for_adapter(self, adapter: LanguageAdapter) -> int:
        """Scanner LOC this adapter should have covered, family-folded like ``_adapter_names_for``."""
        configured = set(_adapter_names_for(self.programming_langs))
        total = 0
        for pl in self.programming_langs:
            mapped = _lang_to_adapter_name(pl.language)
            if mapped is None:
                continue
            # JavaScript LOC is read by the TypeScript engine whenever that one owns the family.
            if mapped == AdapterName.JAVASCRIPT and AdapterName.JAVASCRIPT not in configured:
                mapped = AdapterName.TYPESCRIPT
            if mapped == adapter.language:
                total += pl.size
        return total

    def _run_full_analysis(self, engine_config: EngineConfig, engine_client: LSPClient) -> dict:
        """Run a full analysis using the engine pipeline.

        Returns the dict shape expected by analyze():
            call_graph, class_hierarchies, package_relations, references, source_files, diagnostics

        Uses ``engine_config.source_files`` when the scanner authoritatively
        resolved file membership (currently TypeScript via ``tsc --showConfig``);
        otherwise the adapter walks ``engine_config.project_path`` and applies
        the ignore manager.
        """
        adapter, project_path = engine_config.adapter, engine_config.project_path
        source_files = engine_config.source_files or adapter.discover_source_files(project_path, self.ignore_manager)

        if not source_files:
            logger.warning(f"No source files found for {adapter.language} in {project_path}")
            return {
                "call_graph": CallGraph(language=adapter.language),
                "class_hierarchies": {},
                "package_relations": {},
                "references": [],
                "source_files": [],
                "diagnostics": {},
            }

        logger.info(f"Analyzing {len(source_files)} {adapter.language} files")

        t_build_start = time.monotonic()
        builder = CallGraphBuilder(
            engine_client,
            adapter,
            project_path,
            self.repository_path,
        )
        engine_result = builder.build(source_files)
        logger.info(f"CallGraphBuilder.build() for {adapter.language}: {time.monotonic() - t_build_start:.1f}s")
        # Why: the builder tolerates failed requests, so a server lost midway would otherwise yield a hollow graph.
        engine_client.check_alive()
        if adapter.fail_on_empty_symbols is True and not builder.symbol_table.symbols:
            raise StaticAnalysisFatalError(
                f"{adapter.language} analysis produced 0 symbols across {len(source_files)} source files in "
                f"{project_path}. This usually means the language server failed to load the workspace; "
                "not caching empty analysis."
            )

        t_convert = time.monotonic()
        result = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter, self.ignore_manager)
        logger.info(f"convert_to_codeboarding_format for {adapter.language}: {time.monotonic() - t_convert:.1f}s")
        return result

    def _validate_analysis_results(self, results: StaticAnalysisResults) -> None:
        """Reject a result with no language, or a non-empty language bucket with zero symbols, before it is cached."""
        # Why: a language that fails is left out, so every one failing (a server that would not start) reads as empty.
        attempted = sorted({config.adapter.language for config in self._engine_configs if config.source_files})
        if attempted and not results.get_languages():
            raise RuntimeError(
                f"No language could be analysed (attempted: {', '.join(attempted)}); see the errors above"
            )
        # Configs, not clients: when each server lives only for its own analysis,
        # none are live by the time this runs.
        for engine_config in self._engine_configs:
            adapter = engine_config.adapter
            if adapter.fail_on_empty_symbols is not True:
                continue
            language = adapter.results_language
            source_files = results.get_source_files(language)
            if not source_files:
                continue
            try:
                node_count = len(results.get_cfg(language).nodes)
            except ValueError:
                node_count = 0
            if node_count == 0:
                raise StaticAnalysisFatalError(
                    f"{adapter.language} analysis has 0 symbols across {len(source_files)} source files. "
                    "Delete any stale .codeboarding/static_analysis.pkl after fixing the SDK/LSP issue; "
                    "not caching empty analysis."
                )


def get_static_analysis(
    repo_path: Path,
    cache_dir: Path,
    skip_cache: bool = False,
    source_sha: str | None = None,
    changed_files: set[Path] | None = None,
) -> StaticAnalysisResults:
    """CLI orchestrator: get static analysis results with full LSP lifecycle management.

    Starts LSP clients, runs analysis, and stops clients.

    Args:
        repo_path: Path to the repository to analyze.
        cache_dir: Directory for the pkl + sha pair. Pass
            ``get_artifact_dir(repo_path)`` for the canonical location, or a
            per-branch override.
        skip_cache: If True, bypass the SHA-tagged pkl warm-start and re-LSP
            the entire repository from scratch.
        source_sha: Canonical source-state identifier used in cache diagnostics.

    Returns:
        StaticAnalysisResults reflecting the live source state.
    """
    install_workspace_dependencies(repo_path)  # before the analyzer: its tsconfig scan reads what ``extends`` names
    analyzer = StaticAnalyzer(repo_path, changed_files=changed_files, resident_servers=False)
    with analyzer:
        results = analyzer.analyze(
            cache_dir=cache_dir,
            skip_cache=skip_cache,
            source_sha=source_sha,
        )
    results.diagnostics = analyzer.collected_diagnostics
    return results
