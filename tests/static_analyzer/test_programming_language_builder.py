"""Tests for ``ProgrammingLanguageBuilder`` LSP routing.

Pins bug H1 (C-only repos never started clangd): tokei reports ``"C"`` with
``.c`` suffixes must resolve to ``lsp_server_key="cpp"`` and pull clangd's
command, not return ``server_commands=None``.
"""

from __future__ import annotations

import pytest

from static_analyzer import _lang_to_adapter_name
from static_analyzer.constants import (
    LANGUAGE_EXTENSIONS,
    LANGUAGE_TO_LSP_CONFIG_KEY,
    SOURCE_EXTENSION_TO_LANGUAGE,
    TOKEI_LANGUAGE_TO_LSP_CONFIG_KEY,
    Language,
)
from static_analyzer.programming_language import ProgrammingLanguageBuilder
from vscode_constants import VSCODE_CONFIG


@pytest.fixture
def builder() -> ProgrammingLanguageBuilder:
    return ProgrammingLanguageBuilder(VSCODE_CONFIG["lsp_servers"])


class TestTokeiCFamilyResolvesToCpp:
    """tokei splits C/C++ into four language strings; all must route to clangd."""

    @pytest.mark.parametrize(
        "tokei_language, suffix",
        [
            ("C", ".c"),
            ("C++", ".cpp"),
            ("C Header", ".h"),
            ("C++ Header", ".hpp"),
        ],
    )
    def test_routes_to_cpp_lsp_key(self, builder: ProgrammingLanguageBuilder, tokei_language: str, suffix: str) -> None:
        pl = builder.build(
            tokei_language=tokei_language,
            code_count=100,
            percentage=10.0,
            file_suffixes={suffix},
        )
        assert pl.lsp_server_key == "cpp"
        assert pl.is_supported_lang() is True
        assert pl.server_commands == ["clangd"]

    def test_c_only_suffix_set_still_starts_clangd(self, builder: ProgrammingLanguageBuilder) -> None:
        """The H1 regression: ``.c`` alone (no ``.cpp`` sibling) must work."""
        pl = builder.build(tokei_language="C", code_count=500, percentage=99.0, file_suffixes={".c"})
        assert pl.is_supported_lang() is True
        assert pl.server_commands == ["clangd"]
        assert ".c" in pl.suffixes


class TestExtensionFallbackUsesCanonicalSource:
    """The extension fallback path is sourced from ``SOURCE_EXTENSION_TO_LANGUAGE``
    so adapter file-extension sets and scanner LSP routing cannot drift.
    """

    def test_unknown_tokei_name_with_c_suffix_still_resolves(self, builder: ProgrammingLanguageBuilder) -> None:
        # Even if tokei evolves to emit an unrecognised name, a ``.c`` suffix
        # alone routes through the extension index back to cpp.
        pl = builder.build(
            tokei_language="SomethingNewTokeiAdded",
            code_count=100,
            percentage=10.0,
            file_suffixes={".c"},
        )
        assert pl.lsp_server_key == "cpp"
        assert pl.server_commands == ["clangd"]

    def test_extension_index_covers_every_language_extension(self, builder: ProgrammingLanguageBuilder) -> None:
        # Every extension in LANGUAGE_EXTENSIONS must be resolvable to an
        # lsp_config_key. This is what prevented drift in the first place.
        for ext in SOURCE_EXTENSION_TO_LANGUAGE:
            assert ext in builder.get_supported_extensions(), (
                f"{ext} missing from ProgrammingLanguageBuilder._extension_to_lsp; "
                "drift between LANGUAGE_EXTENSIONS and the LSP routing index."
            )

    def test_javascript_extensions_route_to_typescript_lsp(self, builder: ProgrammingLanguageBuilder) -> None:
        # JS adapter shares the typescript-language-server entry; the routing
        # must preserve that mapping.
        pl = builder.build(
            tokei_language="JavaScript",
            code_count=100,
            percentage=10.0,
            file_suffixes={".js"},
        )
        assert pl.lsp_server_key == "typescript"


class TestConstantsRegression:
    """Pins the constants the H1 fix depends on."""

    def test_c_extension_maps_to_cpp_language(self) -> None:
        assert SOURCE_EXTENSION_TO_LANGUAGE[".c"] is Language.CPP

    def test_c_extension_listed_in_cpp_lsp_config(self) -> None:
        """``ProgrammingLanguageBuilder.build`` merges ``config['file_extensions']``
        into the resulting ``suffixes`` list. ``.c`` must be present so a
        pure-C project's ``get_suffix_pattern()`` actually globs ``*.c``.
        """
        assert ".c" in VSCODE_CONFIG["lsp_servers"]["cpp"]["file_extensions"]

    def test_language_to_lsp_config_key_covers_every_language(self) -> None:
        assert set(LANGUAGE_TO_LSP_CONFIG_KEY) == set(Language)

    def test_tokei_alias_map_targets_real_lsp_keys(self) -> None:
        valid = set(LANGUAGE_TO_LSP_CONFIG_KEY.values())
        for alias, target in TOKEI_LANGUAGE_TO_LSP_CONFIG_KEY.items():
            assert target in valid, f"{alias} -> {target} is not a real lsp_config_key"

    def test_cpp_lsp_config_extensions_match_language_extensions(self) -> None:
        """``VSCODE_CONFIG['cpp']['file_extensions']`` must stay aligned with
        ``LANGUAGE_EXTENSIONS[Language.CPP]`` — that drift is the H1 bug.
        """
        cpp_lsp = set(VSCODE_CONFIG["lsp_servers"]["cpp"]["file_extensions"])
        cpp_canonical = set(LANGUAGE_EXTENSIONS[Language.CPP])
        assert cpp_canonical == cpp_lsp, (
            f"LSP config vs LANGUAGE_EXTENSIONS drift. "
            f"Missing from LSP: {cpp_canonical - cpp_lsp}. Extra: {cpp_lsp - cpp_canonical}."
        )


class TestLangToAdapterNameRoutesCFamily:
    """``_lang_to_adapter_name`` routes tokei language strings to adapter
    registry keys. The H1 fix adds ``c`` and ``c header`` for pure-C repos.
    """

    @pytest.mark.parametrize(
        "tokei_language",
        ["c", "C", "cpp", "Cpp", "c++", "C++", "c header", "C Header", "c++ header", "C++ Header"],
    )
    def test_c_family_resolves_to_cpp_adapter(self, tokei_language: str) -> None:
        assert _lang_to_adapter_name(tokei_language) == "Cpp"
