import logging
from pathlib import Path
from typing import List

from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner

logger = logging.getLogger(__name__)


def create_clients(programming_languages: List[ProgrammingLanguage], repository_path: Path) -> list:
    clients = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        try:
            if pl.language in ['TypeScript']:
                # For TypeScript, scan for multiple project configurations (mono-repo support)
                config_scanner = TypeScriptConfigScanner(repository_path)
                typescript_projects = config_scanner.find_typescript_projects()
                
                if typescript_projects:
                    # Create a separate client for each TypeScript project found
                    for project_path in typescript_projects:
                        logger.info(f"Creating TypeScript client for project at: {project_path.relative_to(repository_path)}")
                        clients.append(TypeScriptClient(language=pl, project_path=project_path))
                else:
                    # Fallback: No config files found, use repository root
                    logger.info("No TypeScript config files found, using repository root")
                    clients.append(TypeScriptClient(language=pl, project_path=repository_path))
            else:
                clients.append(LSPClient(language=pl, project_path=repository_path))
        except RuntimeError as e:
            logger.error(f"Failed to create LSP client for {pl.language}: {e}")
    return clients
