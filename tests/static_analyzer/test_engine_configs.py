"""Tests for engine-config creation: language→adapter mapping and the
C+Cpp dedup-collapse that keeps clangd running once per project root.
"""

from pathlib import Path
from unittest.mock import patch

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import _create_engine_configs, _lang_to_adapter_name
from static_analyzer.engine.adapters.c_adapter import CAdapter
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter
from static_analyzer.programming_language import ProgrammingLanguage


class TestLangToAdapterName:
    """The mapping accepts every tokei label that should route to clangd."""

    def test_cpp_variants(self):
        assert _lang_to_adapter_name("C++") == "Cpp"
        assert _lang_to_adapter_name("Cpp") == "Cpp"
        assert _lang_to_adapter_name("C++ Header") == "Cpp"

    def test_c_variants(self):
        assert _lang_to_adapter_name("C") == "C"
        assert _lang_to_adapter_name("C Header") == "C"

    def test_unknown_returns_none(self):
        assert _lang_to_adapter_name("brainfuck") is None


def _pl(language: str, suffixes: list[str]) -> ProgrammingLanguage:
    return ProgrammingLanguage(
        language=language,
        size=1000,
        percentage=100.0,
        suffixes=suffixes,
        server_commands=["clangd"],
        lsp_server_key="cpp",
    )


class TestCAndCppCollapse:
    """Pure-C → CAdapter. Pure-C++ → CppAdapter. Mixed → CppAdapter only
    (clangd handles both dialects in one process; spawning twice would
    redundantly index ``.c`` files via ``LANGUAGE_EXTENSIONS[Language.CPP]``).
    """

    def _configs(self, langs: list[ProgrammingLanguage], root: Path):
        # Bypass real ignore-file IO; we only care about the config-selection logic.
        with patch.object(RepoIgnoreManager, "_load_codeboardingignore_patterns", return_value=[]):
            mgr = RepoIgnoreManager(root)
            return _create_engine_configs(langs, root, mgr)

    def test_pure_c_uses_c_adapter(self, tmp_path: Path) -> None:
        configs = self._configs([_pl("C", [".c"]), _pl("C Header", [".h"])], tmp_path)
        assert len(configs) == 1
        assert type(configs[0].adapter) is CAdapter

    def test_pure_cpp_uses_cpp_adapter(self, tmp_path: Path) -> None:
        configs = self._configs([_pl("C++", [".cpp"]), _pl("C++ Header", [".hpp"])], tmp_path)
        assert len(configs) == 1
        assert type(configs[0].adapter) is CppAdapter

    def test_mixed_c_and_cpp_collapses_to_cpp(self, tmp_path: Path) -> None:
        """The CAdapter config for the same root must be dropped — clangd
        running twice would redundantly index ``.c`` files (already in
        ``LANGUAGE_EXTENSIONS[Language.CPP]``).
        """
        configs = self._configs(
            [_pl("C", [".c"]), _pl("C Header", [".h"]), _pl("C++", [".cpp"])],
            tmp_path,
        )
        assert len(configs) == 1
        assert type(configs[0].adapter) is CppAdapter
