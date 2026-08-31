import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.git_ops import get_changed_files_since
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import AdapterName, Language
from static_analyzer.csharp_config_scanner import CSharpConfigScanner
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_recycler import default_memory_budget, per_engine_memory_budget
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import uri_to_path
from static_analyzer.fsharp_config_scanner import FSharpConfigScanner
from static_analyzer.incremental_orchestrator import update_cfg_for_changed_files
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
    resolved file membership (currently TypeScript via ``tsc --showConfig``);
    otherwise the adapter walks ``project_path`` itself in ``_run_full_analysis``.
    """

    adapter: LanguageAdapter
    project_path: Path
    source_files: list[Path] = field(default_factory=list)


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
                    for csharp_config in csharp_projects:
                        logger.info(
                            f"Creating engine config for CSharp ({csharp_config.project_type}) at: "
                            f"{csharp_config.root.relative_to(repository_path)}"
                        )
                        configs.append(EngineConfig(adapter, csharp_config.root))
                else:
                    logger.info("No C# projects detected")

            elif adapter_name == AdapterName.FSHARP:
                fsharp_scanner = FSharpConfigScanner(repository_path, ignore_manager=ignore_manager)
                fsharp_projects = fsharp_scanner.scan()

                if fsharp_projects:
                    for fsharp_config in fsharp_projects:
                        logger.info(
                            f"Creating engine config for FSharp ({fsharp_config.project_type}) at: "
                            f"{fsharp_config.root.relative_to(repository_path)}"
                        )
                        configs.append(EngineConfig(adapter, fsharp_config.root))
                else:
                    logger.info("No F# projects detected")

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
        Language.FSHARP: AdapterName.FSHARP,
        Language.GO: AdapterName.GO,
        Language.JAVA: AdapterName.JAVA,
        Language.PHP: AdapterName.PHP,
        Language.RUST: AdapterName.RUST,
        # Scanner spellings with no ``Language`` member of their own.
        "tsx": AdapterName.TYPESCRIPT,
        "jsx": AdapterName.JAVASCRIPT,
        "c#": AdapterName.CSHARP,
        "f#": AdapterName.FSHARP,
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
        absorb_lock = threading.Lock()
        spawned: list[str] = []
        spawn_failures: list[str] = []
        # Keyed by position in the engine list so results merge in configuration
        # order. The merges replace on key collision, and overlapping configs
        # (nested solution roots) do collide, so completion order would let two
        # identical runs keep different nodes and produce different component IDs.
        completed: dict[int, tuple[Language, dict]] = {}

        def run_one(engine_config: EngineConfig, engine_client: LSPClient | None, order: int = 0) -> None:
            """Analyze one engine. Owns the client's lifetime when given none."""
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
        if not cap:
            for order, (engine_config, engine_client) in enumerate(self._engine_clients):
                run_one(engine_config, engine_client, order)
        else:
            pending = [cfg for cfg in self._engine_configs if cfg.source_files]
            logger.info("Running %d engine(s) with at most %d resident at a time", len(pending), cap)
            # Not ``with``: its ``__exit__`` waits without cancelling, so one
            # engine's fatal error would still drain every queued engine — a full
            # pass over a monorepo — before propagating. Cancel the queue, then
            # wait so each in-flight ``run_one`` still shuts its own client down.
            pool = ThreadPoolExecutor(max_workers=cap)
            try:
                futures = [pool.submit(run_one, cfg, None, order) for order, cfg in enumerate(pending)]
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

        for order in sorted(completed):
            language, analysis = completed[order]
            self._absorb_into_results(results, language, analysis)

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
        """Bring *cached_results* up to date in-memory, scoped to the changed files.

        Per language: determine the changed-file list, hand it to
        ``update_cfg_for_changed_files`` along with the language's portion of the
        cached state, and put the merged result back into a fresh
        ``StaticAnalysisResults``. The cached ``ClusterCache`` is grafted on either
        way, so a language that fell back to a full re-LSP still leaves a baseline.

        Changed-file source: ``self.changed_files`` when set at construction
        (git-free — e.g. the wrapper's fingerprint diff), else ``git diff`` via
        ``get_changed_files_since``. If git fails (*cached_sha* unreachable, a
        non-git frozen copy, or a content-hash SHA that isn't a git object), fall
        back to a full re-LSP for that language so the run still produces valid
        output.
        """
        results = StaticAnalysisResults()
        for engine_config, engine_client in self._live_clients("warm-start"):
            adapter, project_path = engine_config.adapter, engine_config.project_path
            language = adapter.results_language
            cached_lang_dict = self._extract_language_dict(cached_results, language)
            t_lang_start = time.monotonic()
            changed_files = self._changed_files_for_language(project_path, cached_sha, adapter.language)

            if changed_files is None:
                analysis = self._run_full_analysis(engine_config, engine_client)
            else:
                logger.info(f"warmstart {adapter.language}: re-LSPing {len(changed_files)} changed file(s)")
                analysis = update_cfg_for_changed_files(
                    cached_lang_dict, changed_files, adapter, project_path, engine_client, self.ignore_manager
                )

            self._absorb_into_results(results, language, analysis)
            # Both branches, including the full re-LSP: the partition describes cached_sha,
            # which stays a valid delta baseline however the graph was rebuilt. select()
            # drops whatever the re-LSP no longer has.
            try:
                surviving = results.get_cfg(language).nodes
            except ValueError:
                surviving = {}
            results.set_clusters(language, cached_results.get_clusters(language).select(surviving))
            self._collect_diagnostics_for(adapter, engine_client, analysis)
            track_lsp_result(
                language=adapter.language_enum.value,
                loc=self._loc_for_adapter(adapter),
                status="success",
                duration_ms=round((time.monotonic() - t_lang_start) * 1000),
                analysis=analysis,
                diagnostics=self.collected_diagnostics.get(adapter.results_language, {}),
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
            memory_budget_bytes=per_engine_memory_budget(max(max_concurrent_engines(), 1)),
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
