from pathlib import Path
from typing import List
import logging
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient

logger = logging.getLogger(__name__)


def create_clients(programming_languages: List[ProgrammingLanguage], repository_path: Path) -> list:
    clients = []
    for programming_language in programming_languages:
        if not programming_language.is_supported_lang():
            logger.warning(f"Unsupported programming language: {programming_language.language}. Skipping.")
            continue
        if programming_language.language in ['TypeScript']:
            clients.append(TypeScriptClient(
                language=programming_language,
                project_path=repository_path
            ))
        else:
            clients.append(LSPClient(
                language=programming_language,
                project_path=repository_path
            ))
    return clients
