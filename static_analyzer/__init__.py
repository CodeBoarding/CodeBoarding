import logging
from pathlib import Path
from typing import List

from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.typescript_scanner import TypeScriptConfigScanner

logger = logging.getLogger(__name__)


def create_clients(programming_languages: List[ProgrammingLanguage], repository_path: Path) -> list:
    clients = []
    
    # First, scan for TypeScript projects in subdirectories
    ts_scanner = TypeScriptConfigScanner(repository_path)
    ts_projects = ts_scanner.get_project_directories()
    
    # Create clients for each TypeScript project found
    for project_dir in ts_projects:
        try:
            # Create a special TypeScript client for this specific project
            client = TypeScriptClient(
                language=ProgrammingLanguage(language='TypeScript', size=0, percentage=0, suffixes=['.ts', '.tsx', '.js', '.jsx'], server_commands=None),
                project_path=project_dir
            )
            clients.append(client)
            logger.info(f"Created TypeScript client for project: {project_dir}")
        except RuntimeError as e:
            logger.error(f"Failed to create TypeScript client for project {project_dir}: {e}")
    
    # Create clients for other programming languages
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        
        # Skip TypeScript since we already handled it above
        if pl.language in ['TypeScript']:
            continue
            
        try:
            clients.append(LSPClient(language=pl, project_path=repository_path))
        except RuntimeError as e:
            logger.error(f"Failed to create LSP client for {pl.language}: {e}")
    
    return clients
