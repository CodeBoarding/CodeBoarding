import copy
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.git_ops import get_changed_files_since
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language
from static_analyzer.csharp_config_scanner import CSharpConfigScanner
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import uri_to_path
from static_analyzer.java_config_scanner import JavaConfigScanner
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.program_graph import ProgramEdgeKind, ProgramGraph
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


class IncrementalProgramGraphUnavailableError(RuntimeError):
    """Raised when a ProgramGraph cannot be updated safely from its baseline."""


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

    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue

        lang_lower = pl.language.lower()

        # Map CodeBoarding ProgrammingLanguage to engine adapter name
        adapter_name = _lang_to_adapter_name(pl.language)
        if adapter_name is None:
            logger.warning(f"No engine adapter for language: {pl.language}. Skipping.")
            continue

        try:
            adapter = get_adapter(adapter_name)
        except ValueError:
            logger.warning(f"Engine adapter not found for: {adapter_name}. Skipping.")
            continue

        try:
            if lang_lower in (Language.TYPESCRIPT, Language.JAVASCRIPT):
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
                    configs.append(EngineConfig(adapter, repository_path, source_files=union))
                else:
                    logger.info(f"No TypeScript config files found, using repository root for {adapter_name}")
                    configs.append(EngineConfig(adapter, repository_path))

            elif lang_lower == Language.JAVA:
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

            elif lang_lower in (Language.CSHARP, "c#"):
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

            else:
                configs.append(EngineConfig(adapter, repository_path))

        except RuntimeError as e:
            logger.error(f"Failed to create engine config for {pl.language}: {e}")

    return configs


def _lang_to_adapter_name(language: str) -> str | None:
    """Map a ProgrammingLanguage name to the engine adapter registry key."""
    mapping: dict[str, str] = {
        "python": "Python",
        "typescript": "TypeScript",
        "javascript": "JavaScript",
        "tsx": "TypeScript",
        "jsx": "JavaScript",
        "c#": "CSharp",
        "csharp": "CSharp",
        "go": "Go",
        "java": "Java",
        "php": "PHP",
        "rust": "Rust",
    }
    return mapping.get(language.lower())


class StaticAnalyzer:
    """Sole responsibility: Analyze the code using the engine LSP pipeline."""

    def __init__(self, repository_path: Path):
        self.repository_path = repository_path.resolve()
        self.ignore_manager = RepoIgnoreManager(self.repository_path)
        self.programming_langs = ProjectScanner(self.repository_path).scan()
        self._engine_configs = _create_engine_configs(self.programming_langs, self.repository_path, self.ignore_manager)
        self._engine_clients: list[tuple[EngineConfig, LSPClient]] = []
        self.collected_diagnostics: dict[Language, FileDiagnosticsMap] = {}
        self._clients_started: bool = False
        self._cached_results: StaticAnalysisResults | None = None
        # True once ``analyze()`` has produced fresh results this session that
        # the on-disk pkl doesn't have yet. Only ``analyze()`` sets it;
        # ``flush_cache``/``stop_clients`` write the pkl iff it is set. A
        # read-only ``load_cached_analysis`` never flips it, so rehydrating the
        # artifact for inspection can't rewrite (and strip the SHA sidecar of) a
        # pkl this session didn't produce.
        self._results_need_saving: bool = False
        # ``stop_clients`` writes the pkl using ``_pending_source_sha`` as the
        # tag value (a diff-base for the next warm-start, NOT a cache gate).
        # ``analyze()`` updates it on every call so the latest run's SHA
        # always reaches disk — including after warm-start merges.
        self._pending_source_sha: str | None = None
        # ``stop_clients`` writes the pkl into ``_pending_cache_dir``.
        # ``analyze()`` resolves it from its ``cache_dir`` arg, falling back
        # to the default below. Always a real path — never None.
        self._pending_cache_dir: Path = get_artifact_dir(self.repository_path)

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
            attempted.append(adapter.language)
            engine_client: LSPClient | None = None
            try:
                logger.info(f"Starting engine LSP client for {adapter.language} at {project_path}")
                t_start = time.monotonic()
                # Allow adapters to prepare the project before LSP startup
                # (e.g. ``dotnet restore`` so csharp-ls sees framework refs).
                adapter.prepare_project(project_path)
                command = adapter.get_lsp_command(project_path)
                init_options = adapter.get_lsp_init_options(self.ignore_manager)
                extra_env = adapter.get_lsp_env(project_path)
                # Node-based LSPs spawn child ``node`` processes by name; on
                # a Node-less host the embedded runtime's dir must be on PATH.
                ensure_node_on_path(command, extra_env)
                workspace_settings = adapter.get_workspace_settings()
                extra_capabilities = getattr(adapter, "extra_client_capabilities", {}) or {}
                engine_client = LSPClient(
                    command=command,
                    project_root=project_path,
                    init_options=init_options,
                    default_timeout=adapter.get_lsp_default_timeout(),
                    collect_diagnostics=True,
                    extra_env=extra_env,
                    workspace_settings=workspace_settings,
                    extra_client_capabilities=extra_capabilities,
                )
                engine_client.start()
                t_lsp_started = time.monotonic()
                logger.info(f"{adapter.language} LSP start: {t_lsp_started - t_start:.1f}s")

                # Some LSP servers (JDTLS, rust-analyzer) load
                # workspace metadata asynchronously and only respond to
                # cross-file queries once that's complete. Adapters opt in
                # via ``wait_for_workspace_ready`` so the language-name
                # check doesn't keep growing.
                if adapter.wait_for_workspace_ready:
                    engine_client.wait_for_server_ready()
                    adapter.validate_workspace_ready(engine_client)
                    logger.info(f"{adapter.language} workspace ready: {time.monotonic() - t_lsp_started:.1f}s")

                started.append((engine_config, engine_client))

            except Exception as exc:
                logger.exception(
                    f"Failed to start engine LSP client for {adapter.language}; "
                    f"skipping this language and continuing"
                )
                failed_languages.append(adapter.language)
                failed_details.append(f"{adapter.language}: {exc}")
                if engine_client is not None:
                    try:
                        engine_client.shutdown()
                    except Exception:
                        logger.exception(
                            f"Error shutting down partially-started {adapter.language} client during cleanup"
                        )

        if not started:
            self._clients_started = False
            details = f"; failures: {'; '.join(failed_details)}" if failed_details else ""
            raise RuntimeError(
                f"Failed to start any engine LSP client (attempted: {', '.join(attempted) or 'none'}){details}"
            )

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

    def stop_clients(self) -> None:
        """Gracefully shut down all engine LSP server processes. Idempotent.

        Persists the latest ``_cached_results`` to the pkl on the way down so
        downstream ProgramGraph lineage mutations reach disk in one save. Save errors
        are logged but never block teardown.
        """
        if not self._clients_started:
            return
        if self._results_need_saving:
            self.flush_cache()
        for engine_config, client in self._engine_clients:
            try:
                client.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down engine LSP client for {engine_config.adapter.language}: {e}")
        self._engine_clients = []
        self._clients_started = False
        self._cached_results = None

    def flush_cache(self) -> None:
        """Write ``_cached_results`` to the SHA-tagged pkl at ``_pending_cache_dir``.

        No-op unless ``analyze()`` produced results this session. A pkl loaded
        read-only via ``load_cached_analysis`` is never written back, so a flush
        after a bare load can't strip the artifact's SHA sidecar. Called
        automatically by ``stop_clients``; callers that need the pkl on disk
        before teardown (e.g. capture tooling) can invoke this explicitly after
        the run completes.
        """
        if not self._results_need_saving or self._cached_results is None:
            return
        try:
            StaticAnalysisCache(self._pending_cache_dir, self.repository_path).save(
                self._cached_results, source_sha=self._pending_source_sha
            )
            logger.info(f"Saved static analysis run artifact to {self._pending_cache_dir}")
            # Clear so an idempotent second stop_clients (or explicit flush)
            # doesn't rewrite the pkl it just saved.
            self._results_need_saving = False
        except Exception:
            logger.exception("Failed to persist static analysis pkl during stop_clients; continuing teardown")

    def collect_fresh_diagnostics(self) -> dict[Language, FileDiagnosticsMap]:
        """Read current diagnostics from all running LSP clients without re-analyzing.

        The LSP servers accumulate ``textDocument/publishDiagnostics`` notifications
        automatically after ``didChange``.  This method reads the collected
        diagnostics without triggering any new analysis work.
        """
        result: dict[Language, FileDiagnosticsMap] = {}
        for engine_config, client in self._engine_clients:
            diags = client.get_collected_diagnostics()
            if diags:
                result[engine_config.adapter.language_enum] = diags
        return result

    def get_diagnostics_generation(self) -> int:
        """Return the sum of diagnostics generation counters across all LSP clients."""
        return sum(client.get_diagnostics_generation() for _, client in self._engine_clients)

    def load_cached_analysis(
        self,
        artifact_dir: Path | None = None,
        expected_sha: str | None = None,
    ) -> StaticAnalysisResults | None:
        """Rehydrate the on-disk run artifact for read-only reuse, or None if absent/stale.

        Used by health/status consumers that reuse the last analysis's call
        graph without re-analyzing. This never marks results as produced, so a
        subsequent ``flush_cache``/``stop_clients`` is a no-op — rehydrating an
        artifact for inspection can't rewrite (and strip the SHA sidecar of) a
        pkl this session didn't produce.

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
        for engine_config, client in self._engine_clients:
            adapter = engine_config.adapter
            if suffix in adapter.file_extensions:
                # Open + change to ensure the server has the latest content
                client.did_open(file_path, adapter.language_id)
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
        for engine_config, client in self._engine_clients:
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
        for engine_config, _ in self._engine_clients:
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
            (c for engine_config, c in self._engine_clients if suffix in engine_config.adapter.file_extensions), None
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
        3. Pkl present -> load it, scope the warm-start via ``git diff``,
           re-LSP just those, merge in memory.
        4. No pkl -> full LSP.

        Persistence is deferred to ``stop_clients`` so downstream mutations
        (such as Infomap lineage snapshots) reach disk in one
        save instead of two. ``source_sha`` is stashed for that save.

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
                logger.info("static_analysis_cache: outcome=miss_absent")
                results = self._run_full_lsp_pass()
            else:
                cached_results, cached_sha = warm_start
                logger.info(
                    "static_analysis_cache: outcome=warmstart (cached_sha=%s, current_sha=%s)",
                    cached_sha,
                    source_sha or "<none>",
                )
                results = self._update_cached_results(cached_results, cached_sha)

        self._validate_analysis_results(results)
        results.diagnostics = self.collected_diagnostics
        self._cached_results = results
        self._pending_source_sha = source_sha
        self._pending_cache_dir = cache_dir
        # Fresh results this session: flush_cache/stop_clients should write them.
        self._results_need_saving = True
        return results

    def _run_full_lsp_pass(self) -> StaticAnalysisResults:
        """Run a fresh LSP analysis for every started engine client.

        Cold path: nothing reusable on disk, so every language re-indexes.
        ``analyze()`` calls this only when the pkl is missing or the caller
        explicitly requested ``skip_cache=True``.
        """
        results = StaticAnalysisResults()
        for engine_config, engine_client in self._engine_clients:
            adapter, project_path = engine_config.adapter, engine_config.project_path
            language = adapter.language_enum
            t_lang_start = time.monotonic()
            try:
                logger.info(f"Starting engine analysis for {adapter.language} in {project_path}")
                analysis = self._run_full_analysis(engine_config, engine_client)
                self._absorb_into_results(results, language, analysis)
                duration_ms = round((time.monotonic() - t_lang_start) * 1000)
                logger.info(f"Engine analysis for {adapter.language} completed in {duration_ms / 1000:.1f}s")
                self._collect_diagnostics_for(adapter, engine_client, analysis)
                track_lsp_result(
                    language=adapter.language_enum.value,
                    loc=self._loc_for_adapter(adapter),
                    status="success",
                    duration_ms=duration_ms,
                    analysis=analysis,
                    diagnostics=self.collected_diagnostics.get(adapter.language_enum, {}),
                )
            except StaticAnalysisFatalError:
                raise
            except Exception as e:
                logger.error(f"Error during engine analysis for {adapter.language}: {e}")
                track_lsp_result(
                    language=adapter.language_enum.value,
                    loc=self._loc_for_adapter(adapter),
                    status="error",
                    duration_ms=round((time.monotonic() - t_lang_start) * 1000),
                    analysis={},
                    diagnostics={},
                )
        summaries = []
        for language in results.get_languages():
            try:
                graph = results.get_program_graph(language)
                node_count = len(graph.symbol_nodes())
                edge_count = len(graph.edges_of_kind(ProgramEdgeKind.CALL))
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
        """Update changed file neighborhoods while retaining the cached graph."""
        results = StaticAnalysisResults()
        for engine_config, engine_client in self._engine_clients:
            adapter, project_path = engine_config.adapter, engine_config.project_path
            language = adapter.language_enum
            try:
                cached_results.get_program_graph(language)
            except ValueError as error:
                raise IncrementalProgramGraphUnavailableError(
                    f"No ProgramGraph baseline is available for {adapter.language}; run a full analysis first"
                ) from error

            if language not in results.results:
                results.results[language] = copy.deepcopy(cached_results.results[language])
                self.collected_diagnostics[language] = copy.deepcopy(cached_results.diagnostics.get(language, {}))

            changed_files = {
                path.resolve()
                for path in self._changed_files_for_language(project_path, cached_sha, adapter.language)
                if path.suffix in adapter.file_extensions and path.resolve().is_relative_to(project_path.resolve())
            }
            if not changed_files:
                continue

            current_source_files = self._source_files_for_config(engine_config)
            baseline_graph = results.get_program_graph(language)
            builder = CallGraphBuilder(engine_client, adapter, project_path)
            scope_files = self._incremental_scope_files(
                baseline_graph,
                changed_files,
                current_source_files,
                builder,
            )
            logger.info(
                "Incremental %s graph update: %d changed files, %d scoped files",
                adapter.language,
                len(changed_files),
                len(scope_files),
            )

            updated_graph = baseline_graph.without_files(str(path) for path in changed_files)
            if scope_files:
                delta = self._run_analysis_for_files(
                    engine_config,
                    engine_client,
                    scope_files,
                    known_source_files=current_source_files,
                )
                delta_graph = delta.get("program_graph")
                if not isinstance(delta_graph, ProgramGraph):
                    raise StaticAnalysisFatalError(
                        f"Incremental analysis for {adapter.language} did not produce a ProgramGraph"
                    )
                updated_graph.merge(delta_graph)
            self._merge_incremental_diagnostics(language, changed_files, engine_client)

            bucket = results.results[language]
            bucket.program_graph = updated_graph
            project_root = project_path.resolve()
            outside_project = [
                path for path in bucket.source_files if not Path(path).resolve().is_relative_to(project_root)
            ]
            bucket.source_files = sorted({*outside_project, *(str(path) for path in current_source_files)})
        return results

    def _source_files_for_config(self, engine_config: EngineConfig) -> list[Path]:
        adapter, project_path = engine_config.adapter, engine_config.project_path
        source_files = engine_config.source_files or adapter.discover_source_files(project_path, self.ignore_manager)
        return sorted(path.resolve() for path in source_files if path.exists())

    @staticmethod
    def _incremental_scope_files(
        baseline_graph: ProgramGraph,
        changed_files: set[Path],
        current_source_files: list[Path],
        builder: CallGraphBuilder,
    ) -> list[Path]:
        current_by_path = {str(path): path for path in current_source_files}
        changed_paths = {str(path) for path in changed_files}
        scope_paths = changed_paths & current_by_path.keys()

        for edge in baseline_graph.edges:
            source_path = baseline_graph.nodes[edge.source].file_path
            target_path = baseline_graph.nodes[edge.target].file_path
            if source_path not in changed_paths and target_path not in changed_paths:
                continue
            if source_path in current_by_path:
                scope_paths.add(source_path)
            if target_path in current_by_path:
                scope_paths.add(target_path)

        inspector = SourceInspector()
        for changed_path in sorted(scope_paths & changed_paths):
            for declaration in inspector.find_import_declarations(Path(changed_path)):
                target = builder.resolve_import_target(declaration, current_source_files)
                if target in current_by_path:
                    scope_paths.add(target)
        return [current_by_path[path] for path in sorted(scope_paths)]

    def _merge_incremental_diagnostics(
        self,
        language: Language,
        changed_files: set[Path],
        engine_client: LSPClient,
    ) -> None:
        changed_paths = {str(path) for path in changed_files}
        retained = {
            path: diagnostics
            for path, diagnostics in self.collected_diagnostics.get(language, {}).items()
            if str(Path(path).resolve()) not in changed_paths
        }
        retained.update(engine_client.get_collected_diagnostics())
        self.collected_diagnostics[language] = retained

    def _changed_files_for_language(self, project_path: Path, cached_sha: str, language: str) -> set[Path]:
        """The warm-start changed-file set scoped to one language's project root.

        ``git diff`` via ``get_changed_files_since``. ``None`` means "detect
        failed / no set" and the caller does a full re-LSP for the language.
        """
        try:
            return set(get_changed_files_since(project_path, cached_sha))
        except Exception as e:
            raise IncrementalProgramGraphUnavailableError(
                f"Cannot diff the {language} ProgramGraph baseline {cached_sha}; run a full analysis"
            ) from e

    def _absorb_into_results(self, results: StaticAnalysisResults, language: Language, analysis: dict) -> None:
        """Persist one language's canonical graph and source-file inventory.

        CFG, hierarchy, references, and package relations are projections of
        ProgramGraph.  Keeping second copies here would let those views drift.
        """
        program_graph = analysis.get("program_graph")
        if program_graph is None:
            raise StaticAnalysisFatalError(f"Analysis for {language.value} did not produce a ProgramGraph")
        results.add_program_graph(language, program_graph)
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
        self.collected_diagnostics[adapter.language_enum] = merged_diags

    def _loc_for_adapter(self, adapter: LanguageAdapter) -> int:
        """Return scanner LOC that should have been covered by this adapter."""
        adapter_name = adapter.language.lower()
        total = 0
        for pl in self.programming_langs:
            mapped = _lang_to_adapter_name(pl.language)
            if mapped is not None and mapped.lower() == adapter_name:
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
        source_files = self._source_files_for_config(engine_config)
        return self._run_analysis_for_files(engine_config, engine_client, source_files)

    def _run_analysis_for_files(
        self,
        engine_config: EngineConfig,
        engine_client: LSPClient,
        source_files: list[Path],
        known_source_files: Collection[Path] = (),
    ) -> dict:
        """Analyze an explicit source-file scope with the active language server."""
        adapter, project_path = engine_config.adapter, engine_config.project_path

        if not source_files:
            logger.warning(f"No source files found for {adapter.language} in {project_path}")
            return {
                "program_graph": ProgramGraph(language=adapter.language.lower()),
                "source_files": [],
                "diagnostics": {},
            }

        logger.info(f"Analyzing {len(source_files)} {adapter.language} files")

        t_build_start = time.monotonic()
        builder = CallGraphBuilder(engine_client, adapter, project_path)
        engine_result = builder.build(source_files)
        if known_source_files:
            engine_result.imports = builder.resolve_import_dependencies(
                source_files,
                list(known_source_files),
            )
        logger.info(f"CallGraphBuilder.build() for {adapter.language}: {time.monotonic() - t_build_start:.1f}s")
        if adapter.fail_on_empty_symbols is True and not builder.symbol_table.symbols:
            raise StaticAnalysisFatalError(
                f"{adapter.language} analysis produced 0 symbols across {len(source_files)} source files in "
                f"{project_path}. This usually means the language server failed to load the workspace; "
                "not caching empty analysis."
            )

        t_convert = time.monotonic()
        result = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter, project_path)
        logger.info(f"convert_to_codeboarding_format for {adapter.language}: {time.monotonic() - t_convert:.1f}s")
        return result

    def _validate_analysis_results(self, results: StaticAnalysisResults) -> None:
        """Reject non-empty language buckets that would otherwise cache zero-symbol output."""
        for engine_config, _ in self._engine_clients:
            adapter = engine_config.adapter
            if adapter.fail_on_empty_symbols is not True:
                continue
            language = adapter.language_enum
            source_files = results.get_source_files(language)
            if not source_files:
                continue
            try:
                node_count = len(results.get_program_graph(language).symbol_nodes())
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
) -> StaticAnalysisResults:
    """CLI orchestrator: get static analysis results with full LSP lifecycle management.

    Starts LSP clients, runs analysis, and stops clients — all in one call.

    Args:
        repo_path: Path to the repository to analyze.
        cache_dir: Directory for the pkl + sha pair. Pass
            ``get_artifact_dir(repo_path)`` for the canonical location, or a
            per-branch override.
        skip_cache: If True, bypass the SHA-tagged pkl warm-start and re-LSP
            the entire repository from scratch.
        source_sha: Canonical source-state identifier (typically a git tree SHA)
            stamped onto the freshly-saved pkl as a diff base for the next
            warm-start.

    Returns:
        StaticAnalysisResults reflecting the live source state.
    """
    analyzer = StaticAnalyzer(repo_path)
    with analyzer:
        results = analyzer.analyze(
            cache_dir=cache_dir,
            skip_cache=skip_cache,
            source_sha=source_sha,
        )
    results.diagnostics = analyzer.collected_diagnostics
    return results
