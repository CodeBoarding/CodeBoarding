from pathlib import Path
from unittest.mock import patch

import pytest
import tree_sitter_language_pack as language_pack

import install
from tool_registry import grammars


@pytest.fixture(autouse=True)
def _fresh_cache():
    grammars.kotlin_language.cache_clear()
    yield
    grammars.kotlin_language.cache_clear()


class TestKotlinLanguage:
    def test_the_grammar_is_loaded_from_the_servers_dir(self, tmp_path: Path):
        loaded = object()
        with (
            patch("tool_registry.grammars.get_servers_dir", return_value=tmp_path),
            patch.object(grammars.language_pack, "configure") as configure,
            patch.object(grammars.language_pack, "downloaded_languages", return_value=["kotlin"]),
            patch.object(grammars.language_pack, "get_language", return_value=loaded),
        ):
            assert grammars.kotlin_language() is loaded
        assert configure.call_args.args[0].cache_dir == str(tmp_path)

    def test_a_grammar_that_cannot_be_fetched_names_the_remedy(self, tmp_path: Path):
        with (
            patch("tool_registry.grammars.get_servers_dir", return_value=tmp_path),
            patch.object(grammars.language_pack, "configure"),
            patch.object(grammars.language_pack, "downloaded_languages", return_value=[]),
            patch.object(grammars.language_pack, "get_language", side_effect=language_pack.DownloadError("offline")),
        ):
            with pytest.raises(RuntimeError, match="codeboarding-setup") as raised:
                grammars.kotlin_language()
        assert isinstance(raised.value.__cause__, language_pack.DownloadError)


class TestSetupDownloadsTheGrammar:
    def test_setup_fetches_kotlin_into_the_servers_dir(self, tmp_path: Path, capsys):
        with (
            patch.object(grammars.language_pack, "configure") as configure,
            patch.object(grammars.language_pack, "download") as download,
        ):
            install.download_grammars(tmp_path)
        assert configure.call_args.args[0].cache_dir == str(tmp_path)
        download.assert_called_once_with(["kotlin"])
        assert "Kotlin grammar: installed" in capsys.readouterr().out

    def test_an_offline_setup_carries_on(self, tmp_path: Path, capsys):
        """Why: an analysis fetches a missing grammar itself, and fails loudly there if it still cannot."""
        with (
            patch.object(grammars.language_pack, "configure"),
            patch.object(grammars.language_pack, "download", side_effect=language_pack.DownloadError("offline")),
        ):
            install.download_grammars(tmp_path)
        assert "not downloaded (offline)" in capsys.readouterr().out
