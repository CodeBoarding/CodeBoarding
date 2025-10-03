import logging
from pathlib import Path
from typing import List

from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.programming_language import ProgrammingLanguage

logger = logging.getLogger(__name__)


def create_clients(programming_languages: List[ProgrammingLanguage], repository_path: Path) -> list:
    clients = []
    for pl in programming_languages:
        if not pl.is_supported_lang():
            logger.warning(f"Unsupported programming language: {pl.language}. Skipping.")
            continue
        try:
            if pl.language in ['TypeScript']:
                clients.append(TypeScriptClient(language=pl, project_path=repository_path))
            else:
                clients.append(LSPClient(language=pl, project_path=repository_path))
        except RuntimeError as e:
            logger.error(f"Failed to create LSP client for {pl.language}: {e}")
    return clients
