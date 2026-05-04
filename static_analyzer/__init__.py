import logging
import time
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
from static_analyzer.graph import CallGraph
from static_analyzer.incremental_orchestrator import update_cfg_for_changed_files
from static_analyzer.java_config_scanner import JavaConfigScanner
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from tool_registry import ensure_node_on_path
from utils import get_artifact_dir

logger = logging.getLogger(__name__)


def _create_engine_configs(
    programming_languages: list[ProgrammingLanguage],
    repository_path: Path,
    ignore_manager: RepoIgnoreManager,
) -> list[tuple[LanguageAdapter, Path]]:
    """Create (adapter, project_path) pairs from detected languages.

    Handles mono-repo support: for TypeScript/Java, scans for multiple
    project configurations and creates one pair per sub-project.
    """
    configs: list[tuple[LanguageAdapter, Path]] = []

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
                    for project_path in typescript_projects:
                        logger.info(
                            f"Creating engine config for {adapter_name} at: "
                            f"{project_path.relative_to(repository_path)}"
                        )
                        configs.append((adapter, project_path))
                else:
                    logger.info(f"No TypeScript config files found, using repository root for {adapter_name}")
                    configs.append((adapter, repository_path))

            elif lang_lower == Language.JAVA:
                java_config_scanner = JavaConfigScanner(repository_path, ignore_manager=ignore_manager)
                java_projects = java_config_scanner.scan()

                if java_projects:
                    for project_config in java_projects:
                        logger.info(
                            f"Creating engine config for Java ({project_config.build_system}) at: "
                            f"{project_config.root.relative_to(repository_path)}"
                        )
                        configs.append((adapter, project_config.root))
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
                        configs.append((adapter, csharp_config.root))
                else:
                    logger.info("No C# projects detected")

            else:
                configs.append((adapter, repository_path))

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
        programming_langs = ProjectScanner(self.repository_path).scan()
        self._engine_configs = _create_engine_configs(programming_langs, self.repository_path, self.ignore_manager)
        self._engine_clients: list[tuple[LanguageAdapter, Path, LSPClient]] = []
        self.collected_diagnostics: dict[Language, FileDiagnosticsMap] = {}
        self._clients_started: bool = False
        self._cached_results: StaticAnalysisResults | None = None

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

        started: list[tuple[LanguageAdapter, Path, LSPClient]] = []
        attempted: list[str] = []
        failed_languages: list[str] = []

        for adapter, project_path in self._engine_configs:
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
                extra_env = adapter.get_lsp_env()
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

                # Some LSP servers (JDTLS, rust-analyzer, csharp-ls) load
                # workspace metadata asynchronously and only respond to
                # cross-file queries once that's complete. Adapters opt in
                # via ``wait_for_workspace_ready`` so the language-name
                # check doesn't keep growing.
                if adapter.wait_for_workspace_ready:
                    engine_client.wait_for_server_ready()
                    logger.info(f"{adapter.language} workspace ready: {time.monotonic() - t_lsp_started:.1f}s")

                started.append((adapter, project_path, engine_client))

            except Exception:
                logger.exception(
                    f"Failed to start engine LSP client for {adapter.language}; "
                    f"skipping this language and continuing"
                )
                failed_languages.append(adapter.language)
                if engine_client is not None:
                    try:
                        engine_client.shutdown()
                    except Exception:
                        logger.exception(
                            f"Error shutting down partially-started {adapter.language} client during cleanup"
                        )

        if not started:
            self._clients_started = False
            raise RuntimeError(f"Failed to start any engine LSP client (attempted: {', '.join(attempted) or 'none'})")

        if failed_languages:
            logger.warning(
                f"Proceeding with partial LSP coverage. "
                f"Failed: {', '.join(failed_languages)}. "
                f"Started: {', '.join(a.language for a, _, _ in started)}"
            )

        self._engine_clients = started
        self._clients_started = True

    def stop_clients(self) -> None:
        """Gracefully shut down all engine LSP server processes. Idempotent."""
        if not self._clients_started:
            return
        for adapter, _, client in self._engine_clients:
            try:
                client.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down engine LSP client for {adapter.language}: {e}")
        self._engine_clients = []
        self._clients_started = False
        self._cached_results = None

    def collect_fresh_diagnostics(self) -> dict[str, FileDiagnosticsMap]:
        """Read current diagnostics from all running LSP clients without re-analyzing.

        The LSP servers accumulate ``textDocument/publishDiagnostics`` notifications
        automatically after ``didChange``.  This method reads the collected
        diagnostics without triggering any new analysis work.
        """
        result: dict[str, FileDiagnosticsMap] = {}
        for adapter, _, client in self._engine_clients:
            diags = client.get_collected_diagnostics()
            if diags:
                result[adapter.language] = diags
        return result

    def get_diagnostics_generation(self) -> int:
        """Return the sum of diagnostics generation counters across all LSP clients."""
        return sum(client.get_diagnostics_generation() for _, _, client in self._engine_clients)

    def load_from_disk_cache(
        self,
        artifact_dir: Path | None = None,
        expected_sha: str | None = None,
    ) -> StaticAnalysisResults | None:
        """Load the static-analysis run artifact, or None if absent/stale.

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

    def re_save_with_cluster_cache(self, source_sha: str | None = None) -> None:
        """Re-persist the on-disk pkl after clustering has populated ``CallGraph._cluster_cache``.

        ``analyze()`` saves the pkl right after LSP, *before* anything calls
        ``CallGraph.cluster()``. Once a downstream caller (``DiagramGenerator``)
        runs ``build_all_cluster_results`` and the per-language CFGs warm up
        their ``_cluster_cache``, this method re-saves so the next process
        gets a pkl whose CFG already carries the partition — no re-cluster
        on warm-start.
        """
        if self._cached_results is None:
            logger.warning("re_save_with_cluster_cache: no cached results to save; skipping")
            return
        artifact_dir = get_artifact_dir(self.repository_path)
        StaticAnalysisCache(artifact_dir, self.repository_path).save(self._cached_results, source_sha=source_sha)

    def notify_file_changed(self, file_path: Path, content: str) -> None:
        """Notify the LSP server that the editor has saved new content for a file.

        Sends textDocument/didOpen with the new content to the appropriate
        engine LSP client based on file extension.

        Args:
            file_path: Absolute path to the changed file.
            content:   Full current text content of the file.
        """
        suffix = file_path.suffix
        for adapter, _, client in self._engine_clients:
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
        for adapter, project_path, client in self._engine_clients:
            if suffix in adapter.file_extensions:
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
        for adapter, project_path, _ in self._engine_clients:
            if suffix in adapter.file_extensions:
                return adapter, project_path
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
        client = next((c for adapter, _, c in self._engine_clients if suffix in adapter.file_extensions), None)
        if client is None:
            return []

        try:
            call_sites = SourceInspector().find_call_sites(file_path)
            if not call_sites:
                return []

            queries = [(file_path, line, char) for line, char in call_sites]
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
        skip_cache: bool = False,
        source_sha: str | None = None,
    ) -> StaticAnalysisResults:
        """Analyze the repository, warm-starting from the SHA-tagged pkl when present.

        Flow:

        1. In-memory cache hit -> return.
        2. ``skip_cache=True`` -> full LSP analysis, save pkl tagged with
           *source_sha*.
        3. Pkl present -> load it, ask git for files changed since the pkl's
           tag SHA, re-LSP just those files, merge in memory, re-save the
           pkl tagged with *source_sha*. The pkl is the only persistent
           cache; per-language JSON caches are gone.
        4. No pkl -> full LSP, save pkl tagged with *source_sha*.

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

        artifact_dir = get_artifact_dir(self.repository_path)
        cache = StaticAnalysisCache(artifact_dir, self.repository_path)

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

        self._cached_results = results
        results.diagnostics = self.collected_diagnostics
        logger.info(f"Saving static analysis run artifact to {artifact_dir}")
        cache.save(results, source_sha=source_sha)
        return results

    def _run_full_lsp_pass(self) -> StaticAnalysisResults:
        """Run a fresh LSP analysis for every started engine client.

        Cold path: nothing reusable on disk, so every language re-indexes.
        ``analyze()`` calls this only when the pkl is missing or the caller
        explicitly requested ``skip_cache=True``.
        """
        results = StaticAnalysisResults()
        for adapter, project_path, engine_client in self._engine_clients:
            language = adapter.language
            try:
                t_lang_start = time.monotonic()
                logger.info(f"Starting engine analysis for {language} in {project_path}")
                analysis = self._run_full_analysis(adapter, project_path, engine_client)
                self._absorb_into_results(results, language, analysis)
                logger.info(f"Engine analysis for {language} completed in {time.monotonic() - t_lang_start:.1f}s")
                self._collect_diagnostics_for(adapter, engine_client, analysis)
            except Exception as e:
                logger.error(f"Error during engine analysis for {adapter.language}: {e}")
        logger.info(f"Static analysis complete: {results}")
        return results

    def _update_cached_results(self, cached_results: StaticAnalysisResults, cached_sha: str) -> StaticAnalysisResults:
        """Bring *cached_results* up to date in-memory using git-diff scoping.

        Per language: compute the file list git reports as changed since
        *cached_sha*, hand it to ``update_cfg_for_changed_files`` along with
        the language's portion of the cached state, and put the merged
        result back into a fresh ``StaticAnalysisResults``.

        If ``get_changed_files_since`` fails (e.g. *cached_sha* is unreachable
        — possible after a hard branch switch or shallow-clone fetch), fall
        back to a full re-LSP for that language so the run still produces
        valid output.
        """
        results = StaticAnalysisResults()
        for adapter, project_path, engine_client in self._engine_clients:
            language = adapter.language
            cached_lang_dict = self._extract_language_dict(cached_results, language)
            try:
                changed_files = set(get_changed_files_since(project_path, cached_sha))
            except Exception as e:
                logger.warning(
                    f"get_changed_files_since failed for {language} (cached_sha={cached_sha}): {e}; "
                    "falling back to full re-LSP for this language"
                )
                changed_files = None

            if changed_files is None:
                analysis = self._run_full_analysis(adapter, project_path, engine_client)
            else:
                logger.info(f"warmstart {language}: re-LSPing {len(changed_files)} changed file(s)")
                analysis = update_cfg_for_changed_files(
                    cached_lang_dict, changed_files, adapter, project_path, engine_client, self.ignore_manager
                )

            self._absorb_into_results(results, language, analysis)
            self._collect_diagnostics_for(adapter, engine_client, analysis)
        return results

    def _extract_language_dict(self, cached_results: StaticAnalysisResults, language: str) -> dict:
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
            "diagnostics": cached_results.diagnostics.get(Language(language), {}),
        }

    def _absorb_into_results(self, results: StaticAnalysisResults, language: str, analysis: dict) -> None:
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
        self.collected_diagnostics[Language(adapter.language)] = merged_diags

    def _run_full_analysis(
        self,
        adapter: LanguageAdapter,
        project_path: Path,
        engine_client: LSPClient,
    ) -> dict:
        """Run a full analysis using the engine pipeline.

        Returns the dict shape expected by analyze():
            call_graph, class_hierarchies, package_relations, references, source_files, diagnostics
        """
        source_files = adapter.discover_source_files(project_path, self.ignore_manager)

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
        builder = CallGraphBuilder(engine_client, adapter, project_path)
        engine_result = builder.build(source_files)
        logger.info(f"CallGraphBuilder.build() for {adapter.language}: {time.monotonic() - t_build_start:.1f}s")

        t_convert = time.monotonic()
        result = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter)
        logger.info(f"convert_to_codeboarding_format for {adapter.language}: {time.monotonic() - t_convert:.1f}s")
        return result


def get_static_analysis(
    repo_path: Path,
    skip_cache: bool = False,
    source_sha: str | None = None,
) -> StaticAnalysisResults:
    """CLI orchestrator: get static analysis results with full LSP lifecycle management.

    Starts LSP clients, runs analysis, and stops clients — all in one call.

    Args:
        repo_path: Path to the repository to analyze.
        skip_cache: If True, bypass the SHA-tagged pkl warm-start and re-LSP
            the entire repository from scratch.
        source_sha: Canonical source-state identifier (typically a git tree SHA)
            stamped onto the freshly-saved pkl so the next run can use it as
            a diff base.

    Returns:
        StaticAnalysisResults reflecting the live source state.
    """
    analyzer = StaticAnalyzer(repo_path)
    with analyzer:
        results = analyzer.analyze(skip_cache=skip_cache, source_sha=source_sha)
    results.diagnostics = analyzer.collected_diagnostics
    return results
