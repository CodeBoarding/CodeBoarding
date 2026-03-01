import logging
from pathlib import Path

from repo_utils import get_git_commit_hash
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_change_analyzer import ChangeClassification
from static_analyzer.constants import Language
from static_analyzer.incremental_orchestrator import IncrementalAnalysisOrchestrator
from static_analyzer.java_config_scanner import JavaConfigScanner
from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.lsp_client.java_client import JavaClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from utils import get_cache_dir

logger = logging.getLogger(__name__)


def create_clients(
    programming_languages: list[ProgrammingLanguage],
    repository_path: Path,
    ignore_manager: RepoIgnoreManager,
) -> list[LSPClient]:
    clients: list[LSPClient] = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue

        lang_lower = pl.language.lower()

        try:
            if lang_lower in (Language.TYPESCRIPT, Language.JAVASCRIPT):
                # For TypeScript/JS, scan for multiple project configurations (mono-repo support)
                ts_config_scanner = TypeScriptConfigScanner(repository_path, ignore_manager=ignore_manager)
                typescript_projects = ts_config_scanner.find_typescript_projects()

                if typescript_projects:
                    # Create a separate client for each TypeScript project found
                    for project_path in typescript_projects:
                        logger.info(
                            f"Creating TypeScript client for project at: {project_path.relative_to(repository_path)}"
                        )
                        clients.append(
                            TypeScriptClient(
                                language=pl,
                                project_path=project_path,
                                ignore_manager=ignore_manager,
                            )
                        )
                else:
                    # Fallback: No config files found, use repository root
                    logger.info("No TypeScript config files found, using repository root")
                    clients.append(
                        TypeScriptClient(
                            language=pl,
                            project_path=repository_path,
                            ignore_manager=ignore_manager,
                        )
                    )
            elif lang_lower == Language.JAVA:
                # For Java, scan for multiple project configurations (Maven, Gradle, etc.)
                java_config_scanner = JavaConfigScanner(repository_path, ignore_manager=ignore_manager)
                java_projects = java_config_scanner.scan()

                if java_projects:
                    # Create a separate client for each Java project found
                    for project_config in java_projects:
                        logger.info(
                            f"Creating Java client for {project_config.build_system} project at: "
                            f"{project_config.root.relative_to(repository_path)}"
                        )
                        clients.append(
                            JavaClient(
                                project_path=project_config.root,
                                language=pl,
                                project_config=project_config,
                                ignore_manager=ignore_manager,
                            )
                        )
                else:
                    logger.info("No Java projects detected")
            elif lang_lower in (Language.PYTHON, Language.GO, Language.PHP):
                # Languages that use the standard LSPClient
                clients.append(
                    LSPClient(
                        language=pl,
                        project_path=repository_path,
                        ignore_manager=ignore_manager,
                    )
                )
            else:
                # Fallback for any other supported languages
                clients.append(
                    LSPClient(
                        language=pl,
                        project_path=repository_path,
                        ignore_manager=ignore_manager,
                    )
                )
        except RuntimeError as e:
            logger.error(f"Failed to create LSP client for {pl.language}: {e}")
    return clients


class StaticAnalyzer:
    """Sole responsibility: Analyze the code using LSP clients."""

    def __init__(self, repository_path: Path):
        self.repository_path = repository_path.resolve()
        self.ignore_manager = RepoIgnoreManager(self.repository_path)
        programming_langs = ProjectScanner(self.repository_path).scan()
        self.clients = create_clients(programming_langs, self.repository_path, self.ignore_manager)
        self.collected_diagnostics: dict[str, FileDiagnosticsMap] = {}
        self._clients_started: bool = False

    def __enter__(self) -> "StaticAnalyzer":
        self.start_clients()
        return self

    def __exit__(self, _exc_type: type | None, _exc_val: Exception | None, _exc_tb: object | None) -> None:
        self.stop_clients()

    def start_clients(self) -> None:
        """Start all LSP server processes.

        Call once before invoking analyze() or analyze_with_cluster_changes().
        Idempotent — safe to call even if clients are already running.
        """
        if self._clients_started:
            logger.info(f"Clients already started for {self.repository_path}, skipping start.")
            return

        started_clients: list[LSPClient] = []
        for client in self.clients:
            try:
                logger.info(f"Starting LSP client for {client.language.language}")
                client.start()
                started_clients.append(client)
                if isinstance(client, JavaClient):
                    client.wait_for_import()  # timeout auto-computed based on project size
            except Exception as e:
                logger.exception(f"Failed to start LSP client for {client.language.language}")
                # Clean up already-started clients
                for started in reversed(started_clients):
                    try:
                        started.close()
                    except Exception:
                        logger.exception(f"Error closing client for {started.language.language}")
                self._clients_started = False
                raise RuntimeError(f"Failed to start LSP client for {client.language.language}") from e

        self._clients_started = True

    def stop_clients(self) -> None:
        """Gracefully shut down all LSP server processes.

        Call when you are done with all analysis — e.g. at the end of a CLI run
        or when the IDE session is torn down. Idempotent.
        """
        if not self._clients_started:
            return
        for client in self.clients:
            try:
                client.close()
            except Exception as e:
                logger.error(f"Error closing LSP client for {client.language.language}: {e}")
        self._clients_started = False

    def notify_file_changed(self, file_path: Path, content: str) -> None:
        """Notify the LSP server that the editor has saved new content for a file.

        Sends textDocument/didOpen (so pyright registers the file if it is not
        already open) followed by textDocument/didChange (so pyright re-type-checks
        with the new content and emits a fresh publishDiagnostics notification).

        The LSP spec requires a file to be open before didChange is sent.  After
        the analysis loop closes all files, a bare didChange would be silently
        ignored by pyright, which is why we always re-open first.

        The resulting publishDiagnostics notification is captured by the reader
        thread into ``client.diagnostics`` and will be picked up by the next
        call to ``analyze()`` / ``refresh_health_report()``.

        Args:
            file_path: Absolute path to the changed file.
            content:   Full current text content of the file.
        """
        for client in self.clients:
            handled_suffixes = {s.lstrip("*").lstrip(".") for s in client.language_suffix_pattern}
            if file_path.suffix.lstrip(".") not in handled_suffixes:
                continue
            file_uri = file_path.as_uri()
            # Re-open the file so pyright tracks it.  Strictly, LSP 3.17 says a
            # second didOpen for the same URI is an error, but pyright tolerates
            # it in practice (it simply resets its internal state for the file).
            # A proper fix would track open-file state and skip the didOpen when
            # the file is already open, but for now this works with pyright.
            client._send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": client.language_id,
                        "version": 1,
                        "text": content,
                    }
                },
            )
            # Send the change so pyright re-type-checks with the new content.
            client._send_notification(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": file_uri, "version": 2},
                    "contentChanges": [{"text": content}],
                },
            )
            logger.debug(f"Sent didOpen+didChange for {file_path} to {client.language.language} LSP")

    def analyze(self, cache_dir: Path | None = None) -> StaticAnalysisResults:
        """
        Analyze the repository using LSP clients.

        Clients must be running before calling this method. Use start_clients() or
        the context manager (``with StaticAnalyzer(...) as sa:``) to start them.
        get_static_analysis() does this automatically for CLI callers.

        Args:
            cache_dir: Optional cache directory for incremental analysis.
                      If provided, uses git-based incremental analysis per client.
                      If None, performs full analysis without caching.

        Returns:
            StaticAnalysisResults containing all analysis data.
        """
        if not self._clients_started:
            raise RuntimeError(
                "LSP clients are not running. Call start_clients() or use StaticAnalyzer as a context manager "
                "('with StaticAnalyzer(...) as sa:') before calling analyze()."
            )
        results = StaticAnalysisResults()
        for client in self.clients:
            try:
                logger.info(f"Starting static analysis for {client.language.language} in {self.repository_path}")

                # Determine cache path for this client if caching is enabled
                cache_path = None
                if cache_dir is not None:
                    cache_dir = Path(cache_dir)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    # Create unique cache file per client (language + project path hash)
                    client_id = f"{client.language.language.lower()}"
                    cache_path = cache_dir / f"incremental_cache_{client_id}.json"
                    if cache_path.exists():
                        logger.info(f"Using incremental cache: {cache_path}")
                    else:
                        logger.info(f"Cache path configured but no cache exists at: {cache_path}")

                # Use incremental orchestrator when cache is available
                if cache_dir is not None and cache_path is not None:
                    orchestrator = IncrementalAnalysisOrchestrator()
                    analysis = orchestrator.run_incremental_analysis(client, cache_path, analyze_cluster_changes=False)
                else:
                    analysis = client.build_static_analysis()

                results.add_references(client.language.language, analysis.get("references", []))
                # Ensure call_graph is a CallGraph object, not a list
                call_graph = analysis.get("call_graph")
                if call_graph is None:
                    from static_analyzer.graph import CallGraph

                    call_graph = CallGraph()
                results.add_cfg(client.language.language, call_graph)
                results.add_class_hierarchy(client.language.language, analysis.get("class_hierarchies", {}))
                results.add_package_dependencies(client.language.language, analysis.get("package_relations", {}))
                results.add_source_files(client.language.language, analysis.get("source_files", []))

                # Collect diagnostics for health checks.
                #
                # Strategy: start with the cache (covers files not re-opened this
                # session), then overlay the LSP client's in-memory diagnostics
                # (which reflect the latest notify_file_changed content).
                #
                # The in-memory dict is the ground truth for any file that has
                # been opened during this session: if a file is present there,
                # its entry replaces the cached one even if the value is "no
                # issues" (i.e. the file key was removed by an empty
                # publishDiagnostics notification).  Files that were never
                # opened remain covered by the cache.
                cache_diags: dict = analysis.get("diagnostics") or {}
                if cache_diags:
                    logger.info(
                        f"Loaded {len(cache_diags)} files with diagnostics from cache for {client.language.language}"
                    )

                live_diags = client.get_collected_diagnostics()

                # Build the merged view: cache as base, live overwrites per file.
                # Also remove files that pyright cleared (present in cache but
                # no longer in live after the client has seen them at least once).
                merged_diags: dict = dict(cache_diags)
                # Track which files the LSP client has observed this session so
                # we know which cache entries to trust vs evict.
                # We use the union of opened files (those that had a didOpen sent
                # during build_static_analysis or notify_file_changed).
                for file_path, diags in live_diags.items():
                    merged_diags[file_path] = diags  # live wins

                # Evict cache entries for files the client has cleared (empty
                # publishDiagnostics means "file is now clean").  We detect this
                # by checking which files were opened (had diagnostics in a
                # previous live run) but are now absent from live_diags.
                # Use the previous live snapshot stored on the client to identify
                # which files have been actively cleared vs simply not seen.
                previously_live = client._previous_live_diagnostics
                for file_path in previously_live:
                    if file_path not in live_diags:
                        merged_diags.pop(file_path, None)

                # Remember which files the client tracked this cycle.
                client._previous_live_diagnostics = set(live_diags.keys()) | previously_live

                if merged_diags:
                    total_diags = sum(len(d) for d in merged_diags.values())
                    logger.info(
                        f"Diagnostics for {client.language.language}: "
                        f"{len(merged_diags)} files, {total_diags} items "
                        f"(cache={len(cache_diags)}, live={len(live_diags)})"
                    )
                else:
                    logger.debug(f"No diagnostics for {client.language.language}")
                self.collected_diagnostics[client.language.language] = merged_diags
            except Exception as e:
                logger.error(f"Error during analysis with {client.language.language}: {e}")
        logger.info(f"Static analysis complete: {results}")
        return results

    def analyze_with_cluster_changes(self, cache_dir: Path | None = None) -> dict:
        """
        Analyze the repository with cluster change detection.

        This method performs incremental analysis and classifies the magnitude
        of cluster structure changes between the cached state and current state.

        Args:
            cache_dir: Optional cache directory for incremental analysis.
                      If provided, uses git-based incremental analysis per client.
                      If None, performs full analysis without caching.

        Returns:
            Dictionary containing:
            - 'analysis_result': StaticAnalysisResults (or dict for single client)
            - 'cluster_change_result': ClusterChangeResult with detailed metrics
            - 'change_classification': ChangeClassification (SMALL, MEDIUM, BIG)
        """
        if not self.clients:
            return {
                "analysis_result": StaticAnalysisResults(),
                "cluster_change_result": None,
                "change_classification": ChangeClassification.SMALL,
            }

        # For now, we only support single client analysis with cluster changes
        # Multi-client support would require aggregating results across languages
        client = self.clients[0]
        try:
            logger.info(f"Starting cluster change analysis for {client.language.language} in {self.repository_path}")

            # Determine cache path
            cache_path = None
            if cache_dir is not None:
                cache_dir = Path(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                client_id = f"{client.language.language.lower()}"
                cache_path = cache_dir / f"incremental_cache_{client_id}.json"
                if cache_path.exists():
                    logger.info(f"Using incremental cache: {cache_path}")
                else:
                    logger.info(f"Cache path configured but no cache exists at: {cache_path}")

            # Use incremental orchestrator with cluster change analysis
            if cache_path is not None:
                orchestrator = IncrementalAnalysisOrchestrator()
                result = orchestrator.run_incremental_analysis(client, cache_path, analyze_cluster_changes=True)
                # Convert dict analysis_result to StaticAnalysisResults
                if isinstance(result, dict) and "analysis_result" in result:
                    dict_analysis = result["analysis_result"]
                    if isinstance(dict_analysis, dict):
                        language = client.language.language
                        result["analysis_result"] = self._dict_to_static_results(dict_analysis, language)
                        # Add commit hash from orchestrator result
                        if "commit_hash" not in result:
                            result["commit_hash"] = get_git_commit_hash(str(self.repository_path))
                return result
            else:
                # No cache directory configured, perform full analysis without caching
                analysis = client.build_static_analysis()
                language = client.language.language
                static_results = self._dict_to_static_results(analysis, language)
                return {
                    "analysis_result": static_results,
                    "cluster_change_result": None,
                    "change_classification": ChangeClassification.BIG,  # Full analysis = BIG change
                    "commit_hash": get_git_commit_hash(str(self.repository_path)),
                }

        except Exception as e:
            logger.error(f"Error during cluster change analysis: {e}")
            return {
                "analysis_result": StaticAnalysisResults(),
                "cluster_change_result": None,
                "change_classification": ChangeClassification.BIG,
            }

    def _dict_to_static_results(self, analysis_dict: dict, language: str) -> StaticAnalysisResults:
        """Convert analysis dictionary to StaticAnalysisResults."""
        results = StaticAnalysisResults()
        results.add_references(language, analysis_dict.get("references", []))
        call_graph = analysis_dict.get("call_graph")
        if call_graph is None:
            from static_analyzer.graph import CallGraph

            call_graph = CallGraph()
        results.add_cfg(language, call_graph)
        results.add_class_hierarchy(language, analysis_dict.get("class_hierarchies", {}))
        results.add_package_dependencies(language, analysis_dict.get("package_relations", {}))
        source_files = analysis_dict.get("source_files", [])
        results.add_source_files(language, [str(f) for f in source_files])
        return results


def get_static_analysis(
    repo_path: Path, cache_dir: Path | None = None, skip_cache: bool = False
) -> StaticAnalysisResults:
    """
    CLI orchestrator: get static analysis results with full LSP lifecycle management.

    Starts LSP clients, runs analysis, and stops clients — all in one call.
    This is the right entry point for the CLI and DiagramGenerator (one-shot runs).

    Long-lived callers (e.g. extensions of the core) should instead create a StaticAnalyzer
    directly, call start_clients() once, and call stop_clients() when done.

    Args:
        repo_path: Path to the repository to analyze.
        cache_dir: Optional custom cache directory. If None, uses default cache location.
        skip_cache: If True, performs full analysis without using any cache.

    Returns:
        StaticAnalysisResults using incremental cache when available.
    """
    actual_cache_dir = None if skip_cache else (cache_dir if cache_dir is not None else get_cache_dir(repo_path))
    analyzer = StaticAnalyzer(repo_path)
    with analyzer:
        results = analyzer.analyze(cache_dir=actual_cache_dir)
    results.diagnostics = analyzer.collected_diagnostics
    return results
