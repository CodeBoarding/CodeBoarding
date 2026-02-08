import logging
from pathlib import Path

from repo_utils import get_git_commit_hash
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_change_analyzer import ChangeClassification
from static_analyzer.incremental_orchestrator import IncrementalAnalysisOrchestrator
from static_analyzer.constants import Language
from static_analyzer.lsp_client.client import LSPClient, FileDiagnosticsMap
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.lsp_client.java_client import JavaClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from static_analyzer.java_config_scanner import JavaConfigScanner

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

    def analyze(self, cache_dir: Path | None = None) -> StaticAnalysisResults:
        """
        Analyze the repository using LSP clients.

        Args:
            cache_dir: Optional cache directory for incremental analysis.
                      If provided, uses git-based incremental analysis per client.
                      If None, performs full analysis without caching.

        Returns:
            StaticAnalysisResults containing all analysis data.
        """
        results = StaticAnalysisResults()
        for client in self.clients:
            try:
                logger.info(f"Starting static analysis for {client.language.language} in {self.repository_path}")
                client.start()

                # Java-specific: wait for JDTLS to import the project
                if isinstance(client, JavaClient):
                    client.wait_for_import()  # timeout auto-computed based on project size

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

                # Collect diagnostics for health checks (collected during analysis)
                # Stored on the instance (not in StaticAnalysisResults) since diagnostics
                # are ephemeral LSP data only consumed by health checks.
                diagnostics = client.get_collected_diagnostics()
                if diagnostics:
                    logger.info(f"Collected {len(diagnostics)} files with diagnostics for {client.language.language}")
                    total_diags = sum(len(d) for d in diagnostics.values())
                    logger.info(f"Total diagnostic items: {total_diags}")
                    self.collected_diagnostics[client.language.language] = diagnostics
                else:
                    logger.warning(f"No diagnostics collected for {client.language.language}")
            except Exception as e:
                logger.error(f"Error during analysis with {client.language.language}: {e}")
        print(f"Static analysis complete: {results}")
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
            client.start()

            # Java-specific: wait for JDTLS to import the project
            if isinstance(client, JavaClient):
                client.wait_for_import()  # timeout auto-computed based on project size

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
    Orchestrator: Get static analysis results using incremental git-based caching.

    Uses the incremental analysis cache which tracks changes via git commits,
    providing efficient updates when only some files have changed.

    Args:
        repo_path: Path to the repository to analyze.
        cache_dir: Optional custom cache directory. If None, uses default cache location.
        skip_cache: If True, performs full analysis without using any cache.

    Returns:
        StaticAnalysisResults using incremental cache when available.
    """
    if skip_cache:
        # Force full analysis without any caching
        analyzer = StaticAnalyzer(repo_path)
        results = analyzer.analyze(cache_dir=None)
        # Attach diagnostics to results for convenient access
        results.diagnostics = analyzer.collected_diagnostics
        return results

    # Determine actual cache directory to use
    if cache_dir is None:
        # Default behavior: use standard cache location
        actual_cache_dir = repo_path / ".codeboarding" / "cache"
    else:
        actual_cache_dir = cache_dir

    # Use incremental analysis - it handles cache internally via IncrementalAnalysisOrchestrator
    analyzer = StaticAnalyzer(repo_path)
    results = analyzer.analyze(cache_dir=actual_cache_dir)
    # Attach diagnostics to results for convenient access
    results.diagnostics = analyzer.collected_diagnostics
    return results
