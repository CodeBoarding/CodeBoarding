"""Tree-sitter grammars fetched at setup rather than shipped in a wheel.

Kotlin comes from tree-sitter-language-pack, which builds fwcd/tree-sitter-kotlin at the commit
its pinned version names and downloads it on first use.
"""

import logging
from functools import cache
from pathlib import Path

import tree_sitter_language_pack as language_pack
from tree_sitter import Language

from .paths import get_servers_dir

logger = logging.getLogger(__name__)

KOTLIN_GRAMMAR = "kotlin"


def download_kotlin_grammar(servers_dir: Path) -> None:
    """Fetch the Kotlin grammar into *servers_dir*, so no analysis has to download it."""
    language_pack.configure(language_pack.PackConfig(cache_dir=str(servers_dir)))
    try:
        language_pack.download([KOTLIN_GRAMMAR])
    except language_pack.Error as error:
        raise RuntimeError(f"Could not download the Kotlin tree-sitter grammar into {servers_dir}") from error


@cache
def kotlin_language() -> Language:
    """The Kotlin grammar from the setup's cache, downloaded there first when setup has not fetched it."""
    servers_dir = get_servers_dir()
    language_pack.configure(language_pack.PackConfig(cache_dir=str(servers_dir)))
    if KOTLIN_GRAMMAR not in language_pack.downloaded_languages():
        logger.info("Downloading the Kotlin tree-sitter grammar into %s", servers_dir)
    try:
        return language_pack.get_language(KOTLIN_GRAMMAR)
    except language_pack.Error as error:
        raise RuntimeError(
            f"The Kotlin tree-sitter grammar is not in {servers_dir} and could not be downloaded. "
            "Run codeboarding-setup with network access, or point TREE_SITTER_LANGUAGE_PACK_LIBS_DIR "
            "at a copy of the grammar."
        ) from error
