import logging
from pathlib import Path

from repo_utils import get_repo_state_hash
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import AnalysisCache, StaticAnalysisResults
from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.lsp_client.java_client import JavaClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner
from static_analyzer.java_config_scanner import JavaConfigScanner

logger = logging.getLogger(__name__)


def create_clients(
    programming_languages: list[ProgrammingLanguage], repository_path: Path, ignore_manager: RepoIgnoreManager
) -> list[LSPClient]:
    clients: list[LSPClient] = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        try:
            if pl.language.lower() in ["typescript"]:
                # For TypeScript, scan for multiple project configurations (mono-repo support)
                ts_config_scanner = TypeScriptConfigScanner(repository_path, ignore_manager=ignore_manager)
                typescript_projects = ts_config_scanner.find_typescript_projects()

                if typescript_projects:
                    # Create a separate client for each TypeScript project found
                    for project_path in typescript_projects:
                        logger.info(
                            f"Creating TypeScript client for project at: {project_path.relative_to(repository_path)}"
                        )
                        clients.append(
                            TypeScriptClient(language=pl, project_path=project_path, ignore_manager=ignore_manager)
                        )
                else:
                    # Fallback: No config files found, use repository root
                    logger.info("No TypeScript config files found, using repository root")
                    clients.append(
                        TypeScriptClient(language=pl, project_path=repository_path, ignore_manager=ignore_manager)
                    )
            elif pl.language.lower() == "java":
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
            else:
                clients.append(LSPClient(language=pl, project_path=repository_path, ignore_manager=ignore_manager))
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

    def analyze(self) -> StaticAnalysisResults:
        results = StaticAnalysisResults()
        for client in self.clients:
            try:
                logger.info(f"Starting static analysis for {client.language.language} in {self.repository_path}")
                client.start()

                # Java-specific: wait for JDTLS to import the project
                if isinstance(client, JavaClient):
                    client.wait_for_import(timeout=300)  # 5 minute timeout

                analysis = client.build_static_analysis()

                results.add_references(client.language.language, analysis.get("references", []))
                results.add_cfg(client.language.language, analysis.get("call_graph", []))
                results.add_class_hierarchy(client.language.language, analysis.get("class_hierarchies", []))
                results.add_package_dependencies(client.language.language, analysis.get("package_relations", []))
                results.add_source_files(client.language.language, analysis.get("source_files", []))
            except Exception as e:
                logger.error(f"Error during analysis with {client.language.language}: {e}")

        return results


def get_static_analysis(repo_path: Path, cache_dir: Path | None = None) -> StaticAnalysisResults:
    """
    Orchestrator: Get static analysis results, using cache when available.

    Args:
        repo_path: Path to the repository to analyze.
        cache_dir: Optional custom cache directory. Defaults to repo_path/.codeboarding/cache.

    Returns:
        StaticAnalysisResults from cache or fresh analysis.
    """
    if cache_dir is None:
        cache_dir = repo_path / ".codeboarding" / "cache"

    repo_hash = get_repo_state_hash(repo_path)
    cache = AnalysisCache(cache_dir)

    if cached_result := cache.get(repo_hash):
        return cached_result

    result = StaticAnalyzer(repo_path).analyze()

    cache.save(repo_hash, result)

    return result
