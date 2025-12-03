import logging
from pathlib import Path
from typing import List

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.lsp_client import GoClient, LSPClient, TypeScriptClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner

logger = logging.getLogger(__name__)


def create_clients(programming_languages: List[ProgrammingLanguage], repository_path: Path) -> list:
    clients = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        try:
            if pl.language.lower() in ["typescript"]:
                # For TypeScript, scan for multiple project configurations (mono-repo support)
                config_scanner = TypeScriptConfigScanner(repository_path)
                typescript_projects = config_scanner.find_typescript_projects()

                if typescript_projects:
                    # Create a separate client for each TypeScript project found
                    for project_path in typescript_projects:
                        logger.info(
                            f"Creating TypeScript client for project at: {project_path.relative_to(repository_path)}"
                        )
                        clients.append(TypeScriptClient(language=pl, project_path=project_path))
                else:
                    # Fallback: No config files found, use repository root
                    logger.info("No TypeScript config files found, using repository root")
                    clients.append(TypeScriptClient(language=pl, project_path=repository_path))
            elif pl.language.lower() == "go":
                clients.append(GoClient(language=pl, project_path=repository_path))
            else:
                clients.append(LSPClient(language=pl, project_path=repository_path))
        except RuntimeError as e:
            logger.error(f"Failed to create LSP client for {pl.language}: {e}")
    return clients


class StaticAnalyzer:
    def __init__(self, repository_path: Path):
        self.repository_path = repository_path
        programming_langs = ProjectScanner(repository_path).scan()
        self.clients = create_clients(programming_langs, repository_path)

    def analyze(self):
        results = StaticAnalysisResults()
        for client in self.clients:
            try:
                logger.info(f"Starting static analysis for {client.language.language} in {self.repository_path}")
                client.start()

                analysis = client.build_static_analysis()

                results.add_references(client.language.language, analysis.get("references", []))
                results.add_cfg(client.language.language, analysis.get("call_graph", []))
                results.add_class_hierarchy(client.language.language, analysis.get("class_hierarchies", []))
                results.add_package_dependencies(client.language.language, analysis.get("package_relations", []))
                results.add_source_files(client.language.language, analysis.get("source_files", []))
            except Exception as e:
                logger.error(f"Error during analysis with {client.language.language}: {e}")

        return results
