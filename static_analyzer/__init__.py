import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.git_ops import get_changed_files_since
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import StaticAnalysisCache, invalidate_files, _rederive_inherits_edges
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import GRAPH_NODE_TYPES, AdapterName, Language
from static_analyzer.csharp_config_scanner import SOLUTION_GLOBS, CSharpConfigScanner, CSharpProjectConfig
from static_analyzer.dotnet_solution import solution_projects
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.analysis_context import AnalysisContext
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_recycler import default_memory_budget, per_engine_memory_budget
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import uri_to_path
from static_analyzer.incremental_orchestrator import (
    MissingSymbolSnapshotError,
    affected_source_files,
    update_cfg_for_changed_files,
)
from static_analyzer.java_config_scanner import JavaConfigScanner
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from telemetry.events import track_lsp_result
from tool_registry import ensure_node_on_path
from utils import get_artifact_dir

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


class StaticAnalysisFatalError(RuntimeError):
    """Raised when continuing would produce misleading cached analysis."""


MAX_CONCURRENT_ENGINES_ENV_VAR = "CODEBOARDING_MAX_CONCURRENT_ENGINES"


# An engine costs ~3 cores: the server's own peak (~1.9, measured on csharp-ls),
# its dotnet children, and the tree-sitter parsing the same pass runs in Python.
CORES_PER_ENGINE = 3

# Peak RSS per engine, measured on abp at cap=4 (7.09GB across four servers).
ENGINE_MEMORY_FOOTPRINT_BYTES = 2 * 1024**3


def max_concurrent_engines() -> int:
    """How many engine LSP servers may be resident at once. 0 disables the bound.

    Why off by default: the bound hands each client's lifetime to the full pass,
    so warm-start and the editor-facing file queries have no live client to use.
    They refuse to run rather than answer emptily — see ``_live_clients``.
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


def recommended_engine_concurrency(engine_count: int) -> int:
    """Engine concurrency bounded by available work, cores, and memory.

    Why the memory term: ``default_memory_budget()`` is what one server may grow
    to before the recycler restarts it, so ``cap`` servers are expected to hold
    about that much between them. It binds well before CPU on a large host.
    """
    cpu_bound = (os.cpu_count() or CORES_PER_ENGINE) // CORES_PER_ENGINE
    memory_bound = default_memory_budget() // ENGINE_MEMORY_FOOTPRINT_BYTES
    return max(1, min(engine_count, cpu_bound, memory_bound))


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
                    # (Configured Project), so cross-project navigation
                    # via ``references`` keeps working — but only when a
                    # single language-service instance sees both ends of
                    # the edge. Spawning one LSP per tsconfig partitions
                    # the workspace and drops cross-project edges.
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
                    configs.append(EngineConfig(adapter, repository_path, source_files=union))
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
        Language.PHP: AdapterName.PHP,
        Language.RUST: AdapterName.RUST,
        # Scanner spellings with no ``Language`` member of their own.
        "tsx": AdapterName.TYPESCRIPT,
        "jsx": AdapterName.JAVASCRIPT,
        "c#": AdapterName.CSHARP,
    }
    return mapping.get(language.lower())


class StaticAnalyzer:
    """Sole responsibility: Analyze the code using the engine LSP pipeline."""

    def __init__(self, repository_path: Path, changed_files: set[Path] | None = None):
        self.repository_path = repository_path.resolve()
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
            if max_concurrent_engines():
                # Deferred: the full pass owns the lifetime so only a bounded
                # number of servers are resident at once.
                continue
            try:
                started.append((engine_config, self._spawn_engine_client(engine_config)))
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

        if max_concurrent_engines():
            logger.info(
                "%d engine LSP client(s) will start during analysis, at most %d running at a time.",
                len(attempted),
                max_concurrent_engines(),
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
        """Start one engine's LSP server and return it ready for queries."""
        adapter, project_path = engine_config.adapter, engine_config.project_path
        logger.info(f"Starting engine LSP client for {adapter.language} at {project_path}")
        t_start = time.monotonic()
        self._prepare_project_once(engine_config)
        command = adapter.get_lsp_command(project_path)
        init_options = adapter.get_lsp_init_options(self.ignore_manager)
        extra_env = adapter.get_lsp_env(project_path)
        # Node-based LSPs spawn child ``node`` processes by name; on
        # a Node-less host the embedded runtime's dir must be on PATH.
        ensure_node_on_path(command, extra_env)
        engine_client = LSPClient(
            command=command,
            project_root=project_path,
            init_options=init_options,
            default_timeout=adapter.get_lsp_default_timeout(),
            collect_diagnostics=True,
            extra_env=extra_env,
            workspace_settings=adapter.get_workspace_settings(),
            extra_client_capabilities=getattr(adapter, "extra_client_capabilities", {}) or {},
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
        """The started engine clients, refusing callers the concurrency bound cannot serve.

        Why: under ``CODEBOARDING_MAX_CONCURRENT_ENGINES`` the full pass owns
        each client's lifetime, so nothing is resident outside it. Every caller
        below iterates the client list, and an empty one reads as a successful
        empty answer — for warm-start that answer is then persisted over a good
        cache, so a loud refusal is the only safe reading of this state.
        """
        if self._engine_clients or not any(config.source_files for config in self._engine_configs):
            return self._engine_clients
        raise StaticAnalysisFatalError(
            f"{operation} needs live LSP clients, but {MAX_CONCURRENT_ENGINES_ENV_VAR}="
            f"{max_concurrent_engines()} defers them to the full pass. Unset it, or run a full "
            "analysis (skip_cache=True) so the pass owns the servers."
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

        The file must have been opened previously (via ``notify_file_changed``
        or during the initial analysis) so the LSP server has indexed it.

        Args:
            file_path: Absolute path to the file.

        Returns:
            Raw LSP ``DocumentSymbol[]`` response (possibly nested).
            Returns an empty list if no matching client is found.
        """
        suffix = file_path.suffix
        for engine_config, client in self._live_clients("get_file_symbols"):
            if suffix in engine_config.adapter.file_extensions:
                try:
                    symbols = client.document_symbol(file_path)
                    logger.debug(f"Got {len(symbols)} symbols for {file_path}")
                    return symbols
                except Exception:
                    logger.warning(f"Failed to get symbols for {file_path}", exc_info=True)
                    return []
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

        The file must have been opened previously (via ``notify_file_changed``
        or during the initial analysis) so the LSP server has indexed it.

        Args:
            file_path: Absolute path to the source file.

        Returns:
            Deduplicated list of absolute file paths that the file depends on.
            Returns an empty list if no matching client is found or on failure.
        """
        suffix = file_path.suffix
        client = next(
            (
                c
                for engine_config, c in self._live_clients("discover_file_dependencies")
                if suffix in engine_config.adapter.file_extensions
            ),
            None,
        )
        if client is None:
            return []

        try:
            call_sites = SourceInspector().find_call_sites(file_path)
            if not call_sites:
                return []

            queries = [(file_path, site.lsp_line, site.lsp_column) for site in call_sites]
            results, _ = client.send_definition_batch(queries)

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
        except Exception:
            logger.warning(f"Failed to discover dependencies for {file_path}", exc_info=True)
            return []

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
        self._analysis_context = AnalysisContext()
        absorb_lock = threading.Lock()
        spawned: list[str] = []
        spawn_failures: list[str] = []
        # Keyed by position in the engine list so results merge in configuration
        # order. The merges replace on key collision, and overlapping configs
        # (nested solution roots) do collide, so completion order would let two
        # identical runs keep different nodes and produce different component IDs.
        completed: dict[int, tuple[Language, dict]] = {}
        collected: set[int] = set()

        def run_one(engine_config: EngineConfig, engine_client: LSPClient | None, order: int, collect: bool) -> None:
            """Analyze one engine. Owns the client's lifetime when given none."""
            if not collect and order not in collected:
                return
            adapter, project_path = engine_config.adapter, engine_config.project_path
            language = adapter.results_language
            t_lang_start = time.monotonic()
            owned_client: LSPClient | None = None
            try:
                if engine_client is None:
                    try:
                        owned_client = self._spawn_engine_client(engine_config)
                    except Exception as exc:
                        with absorb_lock:
                            spawn_failures.append(f"{adapter.language}: {exc}")
                        raise
                    with absorb_lock:
                        spawned.append(adapter.language)
                    engine_client = owned_client
                logger.info(f"Starting engine analysis for {adapter.language} in {project_path}")
                if collect:
                    self._collect_symbols(engine_config, engine_client)
                    with absorb_lock:
                        collected.add(order)
                    return
                analysis = self._run_full_analysis(engine_config, engine_client)
                duration_ms = round((time.monotonic() - t_lang_start) * 1000)
                logger.info(f"Engine analysis for {adapter.language} completed in {duration_ms / 1000:.1f}s")
                with absorb_lock:
                    completed[order] = (language, analysis)
                    self._collect_diagnostics_for(adapter, engine_client, analysis)
                    track_lsp_result(
                        language=adapter.language_enum.value,
                        loc=self._loc_for_adapter(adapter),
                        status="success",
                        duration_ms=duration_ms,
                        analysis=analysis,
                        diagnostics=self.collected_diagnostics.get(adapter.results_language, {}),
                    )
            except StaticAnalysisFatalError:
                raise
            except Exception as e:
                logger.error(f"Error during engine analysis for {adapter.language}: {e}")
                with absorb_lock:
                    track_lsp_result(
                        language=adapter.language_enum.value,
                        loc=self._loc_for_adapter(adapter),
                        status="error",
                        duration_ms=round((time.monotonic() - t_lang_start) * 1000),
                        analysis={},
                        diagnostics={},
                    )
            finally:
                # Only shut down what this call started; eagerly-started clients
                # stay up for the incremental and file-query paths.
                if owned_client is not None:
                    try:
                        owned_client.shutdown()
                    except Exception:
                        logger.exception(f"Error shutting down {adapter.language} client for {project_path}")

        cap = max_concurrent_engines()
        for collect in (True, False):
            if not collect:
                self._analysis_context.freeze()
            if not cap:
                for order, (engine_config, engine_client) in enumerate(self._engine_clients):
                    run_one(engine_config, engine_client, order, collect)
            else:
                pending = [cfg for cfg in self._engine_configs if cfg.source_files]
                logger.info("Running %d engine(s) with at most %d resident at a time", len(pending), cap)
                pool = ThreadPoolExecutor(max_workers=cap)
                try:
                    futures = [pool.submit(run_one, cfg, None, order, collect) for order, cfg in enumerate(pending)]
                    for future in as_completed(futures):
                        future.result()
                finally:
                    pool.shutdown(wait=True, cancel_futures=True)
                if pending and not spawned:
                    details = f"; failures: {'; '.join(spawn_failures)}" if spawn_failures else ""
                    raise RuntimeError(
                        "Failed to start any engine LSP client "
                        f"(attempted: {', '.join(cfg.adapter.language for cfg in pending)}){details}"
                    )

        if (self._engine_clients or self._engine_configs) and not completed:
            raise StaticAnalysisFatalError("No engine completed edge analysis; refusing to cache an empty graph.")
        for order in sorted(completed):
            language, analysis = completed[order]
            self._absorb_into_results(results, language, analysis)
        self._snapshot_context(results)

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

    def _update_cached_results(
        self,
        cached_results: StaticAnalysisResults,
        cached_sha: str,
    ) -> StaticAnalysisResults:
        """Hydrate once, replace all changed declarations, then requery affected callers."""
        clients = self._live_clients("warm-start")
        self._analysis_context = AnalysisContext()
        results = StaticAnalysisResults()
        baseline: dict[Language, dict] = {}
        carried: dict[Language, dict] = {}
        changes: dict[Language, set[Path]] = {}
        engine_changes: list[set[Path]] = []
        for config, _ in clients:
            adapter = config.adapter
            language = adapter.results_language
            if language not in baseline:
                data = self._extract_language_dict(cached_results, language)
                if data.get("symbols") is None:
                    raise MissingSymbolSnapshotError(
                        f"{language.value} has no symbol snapshot. Run a full analysis first."
                    )
                baseline[language] = data
                self._analysis_context.hydrate(
                    adapter, data["symbols"], data["unresolved_files"], data.get("closed_documents", set())
                )
            detected = self._changed_files_for_language(config.project_path, cached_sha, adapter.language)
            if detected is None:
                raise StaticAnalysisFatalError("Cannot determine incremental changes. Run a full analysis explicitly.")
            scoped = {
                path
                for path in detected
                if path.suffix in adapter.file_extensions
                and not any(path.is_relative_to(root) for root in config.excluded_roots)
                and not self.ignore_manager.should_ignore(path)
            }
            engine_changes.append(scoped)
            changes.setdefault(language, set()).update(scoped)

        for language, data in baseline.items():
            self._analysis_context.tables[language].remove_files(changes[language])
            self._analysis_context.unresolved_files.difference_update(str(p) for p in changes[language])
            carried[language] = invalidate_files(data, changes[language]).analysis.to_dict()
        for config, client in clients:
            language = config.adapter.results_language
            client.refresh_files(changes[language], {Path(p) for p in baseline[language]["source_files"]})
        for (config, client), changed in zip(clients, engine_changes):
            files = sorted(path for path in changed if path.is_file())
            if files:
                self._collect_symbols(EngineConfig(config.adapter, config.project_path, files), client)
            else:
                self._analysis_context.prepared[(config.adapter.results_language, config.project_path.resolve())] = []
        self._analysis_context.freeze()

        for config, client in clients:
            adapter, language = config.adapter, config.adapter.results_language
            t_start = time.monotonic()
            queries = affected_source_files(baseline[language], changes[language], adapter, self._analysis_context)
            queries = {
                p
                for p in queries
                if p.is_relative_to(config.project_path)
                and not any(p.is_relative_to(root) for root in config.excluded_roots)
            }
            carried[language] = update_cfg_for_changed_files(
                carried[language],
                set(),
                adapter,
                config.project_path,
                client,
                self.ignore_manager,
                context=self._analysis_context,
                query_files=queries,
            )
            analysis = carried[language]
            self._collect_diagnostics_for(adapter, client, analysis)
            track_lsp_result(
                language=adapter.language_enum.value,
                loc=self._loc_for_adapter(adapter),
                status="success",
                duration_ms=round((time.monotonic() - t_start) * 1000),
                analysis=analysis,
                diagnostics=self.collected_diagnostics.get(adapter.results_language, {}),
            )
        for language, analysis in carried.items():
            self._absorb_into_results(results, language, analysis)
        self._snapshot_context(results)
        results.incremental_base_results = cached_results
        return results

    def _changed_files_for_language(self, project_path: Path, cached_sha: str, language: str) -> set[Path] | None:
        """Scope supplied or git changes; None makes incremental analysis fail explicitly."""
        if self.changed_files is not None:
            # Scope the repo-wide set to this language's project root so a
            # multi-language repo doesn't re-LSP every changed file per engine.
            return {f for f in self.changed_files if f.is_relative_to(project_path)}
        try:
            return set(get_changed_files_since(project_path, cached_sha))
        except Exception as e:
            logger.warning(
                f"get_changed_files_since failed for {language} (cached_sha={cached_sha}): {e}; "
                "incremental analysis cannot proceed"
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
        bucket = cached_results.results.get(language)
        return {
            "call_graph": cached_cfg,
            "class_hierarchies": class_hierarchies,
            "package_relations": package_relations,
            "references": cached_refs,
            "source_files": cached_source_files,
            "diagnostics": cached_results.diagnostics.get(language, {}),
            "symbols": bucket.symbols if bucket is not None else None,
            "unresolved_files": set(bucket.unresolved_files) if bucket is not None else set(),
            "closed_documents": set(bucket.closed_documents) if bucket is not None else set(),
        }

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

    def _collect_symbols(self, config: EngineConfig, client: LSPClient) -> None:
        files = config.source_files or config.adapter.discover_source_files(config.project_path, self.ignore_manager)
        builder = CallGraphBuilder(client, config.adapter, config.project_path, context=self._analysis_context)
        builder.collect_symbols(files)
        if (
            config.adapter.fail_on_empty_symbols is True
            and files
            and not any(str(path) in builder.symbol_table.file_symbols for path in files)
        ):
            raise StaticAnalysisFatalError(f"{config.adapter.language} produced no symbols in {config.project_path}")

    def _snapshot_context(self, results: StaticAnalysisResults) -> None:
        for language, bucket in results.results.items():
            table = self._analysis_context.tables.get(language)
            if table is not None:
                bucket.symbols = table.snapshot()
                bucket.unresolved_files = self._analysis_context.unresolved_files.intersection(table.file_symbols)
                bucket.closed_documents = {
                    str(p) for p in self._analysis_context.closed_documents if str(p) in table.file_symbols
                }
            graph = bucket.cfg.graph
            if graph is None:
                continue
            participants = {name for edge in graph.edges for name in (edge.get_source(), edge.get_destination())}
            graph = graph.filter(
                lambda node: node.type in GRAPH_NODE_TYPES or node.fully_qualified_name in participants
            )
            bucket.cfg.graph = graph
            hierarchy = {
                name: {**info, "subclasses": []}
                for name, info in (bucket.hierarchy.entries or {}).items()
                if name in graph.nodes
            }
            bucket.hierarchy.entries = hierarchy
            for child, info in hierarchy.items():
                for parent in info.get("superclasses", []):
                    if parent in hierarchy:
                        hierarchy[parent]["subclasses"].append(child)
            _rederive_inherits_edges(graph, hierarchy)
            file_packages: dict[str, str] = {}
            configs = [c for c in self._engine_configs if c.adapter.results_language == language]
            for path in bucket.source_files.paths or []:
                owners = [
                    c
                    for c in configs
                    if Path(path).is_relative_to(c.project_path)
                    and not any(Path(path).is_relative_to(r) for r in c.excluded_roots)
                ]
                if owners:
                    owner = max(owners, key=lambda c: (len(c.project_path.parts), str(c.project_path)))
                    file_packages[path] = owner.adapter.get_package_for_file(Path(path), owner.project_path)
            dependencies: dict[str, dict] = {}
            for path, package in sorted(file_packages.items()):
                dependencies.setdefault(package, {"files": [], "imports": [], "imported_by": []})["files"].append(path)
            for edge in graph.edges:
                src = file_packages.get(edge.src_node.file_path)
                dst = file_packages.get(edge.dst_node.file_path)
                if src is not None and dst is not None and src != dst:
                    if dst not in dependencies[src]["imports"]:
                        dependencies[src]["imports"].append(dst)
                    if src not in dependencies[dst]["imported_by"]:
                        dependencies[dst]["imported_by"].append(src)
            for info in dependencies.values():
                info["imports"].sort()
                info["imported_by"].sort()
            bucket.dependencies.entries = dependencies

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
            memory_budget_bytes=per_engine_memory_budget(max(max_concurrent_engines(), 1)),
            context=getattr(self, "_analysis_context", None),
        )
        engine_result = builder.build(source_files)
        logger.info(f"CallGraphBuilder.build() for {adapter.language}: {time.monotonic() - t_build_start:.1f}s")
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
        """Reject non-empty language buckets that would otherwise cache zero-symbol output."""
        # Configs, not clients: under a concurrency cap the full pass owns each
        # client's lifetime, so none are live by the time this runs.
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
    analyzer = StaticAnalyzer(repo_path, changed_files=changed_files)
    with analyzer:
        results = analyzer.analyze(
            cache_dir=cache_dir,
            skip_cache=skip_cache,
            source_sha=source_sha,
        )
    results.diagnostics = analyzer.collected_diagnostics
    return results
