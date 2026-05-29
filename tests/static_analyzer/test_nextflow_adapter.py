"""Tests for the Nextflow language adapter."""

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import _lang_to_adapter_name
from static_analyzer.constants import Language
from static_analyzer.engine.adapters import ADAPTER_REGISTRY, get_adapter
from static_analyzer.engine.adapters.nextflow_adapter import NextflowAdapter


class TestNextflowAdapterProperties:
    def test_language(self):
        adapter = NextflowAdapter()
        assert adapter.language == "Nextflow"

    def test_file_extensions(self):
        adapter = NextflowAdapter()
        assert adapter.file_extensions == (".nf",)

    def test_language_enum(self):
        adapter = NextflowAdapter()
        assert adapter.language_enum == Language.NEXTFLOW

    def test_language_id(self):
        adapter = NextflowAdapter()
        assert adapter.language_id == "nextflow"

    def test_lsp_command(self):
        adapter = NextflowAdapter()
        assert adapter.lsp_command == ["java", "-jar", "language-server-all.jar"]

    def test_config_key(self):
        adapter = NextflowAdapter()
        assert adapter.config_key == "nextflow"


class TestNextflowLspCommand:
    def test_get_lsp_command_with_jar_path(self):
        adapter = NextflowAdapter()
        mock_config = {
            "nextflow": {
                "command": ["java", "-jar", "language-server-all.jar"],
                "jar_path": "/home/user/.codeboarding/servers/bin/nextflow-lsp/language-server-all.jar",
            }
        }
        with (
            patch("static_analyzer.engine.adapters.nextflow_adapter.get_config", return_value=mock_config),
            patch.object(NextflowAdapter, "_find_java", return_value="java"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            cmd = adapter.get_lsp_command(Path("/some/project"))
        assert cmd[0] == "java"
        assert "-jar" in cmd
        assert cmd[-1] == "/home/user/.codeboarding/servers/bin/nextflow-lsp/language-server-all.jar"

    def test_get_lsp_command_without_jar_path_raises(self):
        """When jar_path is not configured and no well-known path exists, raise RuntimeError."""
        adapter = NextflowAdapter()
        mock_config = {
            "nextflow": {
                "command": ["java", "-jar", "language-server-all.jar"],
            }
        }
        with (
            patch("static_analyzer.engine.adapters.nextflow_adapter.get_config", return_value=mock_config),
            patch("pathlib.Path.is_file", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="Nextflow Language Server"):
                adapter.get_lsp_command(Path("/some/project"))


class TestNextflowFileDiscovery:
    def test_discover_filters_non_nextflow_config(self, tmp_path: Path):
        adapter = NextflowAdapter()

        (tmp_path / "main.nf").write_text("process FOO { }")
        (tmp_path / "nextflow.config").write_text("params { }")
        (tmp_path / "conf").mkdir()
        (tmp_path / "conf" / "base.config").write_text("process { }")

        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "app.config").write_text("some config")

        ignore_manager = RepoIgnoreManager(tmp_path)
        files = adapter.discover_source_files(tmp_path, ignore_manager)
        file_names = [f.name for f in files]

        assert "main.nf" in file_names
        assert "nextflow.config" in file_names
        assert "base.config" in file_names
        assert "app.config" not in file_names


class TestNextflowRegistration:
    def test_adapter_in_registry(self):
        assert "Nextflow" in ADAPTER_REGISTRY

    def test_get_adapter_returns_nextflow(self):
        adapter = get_adapter("Nextflow")
        assert adapter.language == "Nextflow"

    def test_static_analyzer_maps_tokei_language(self):
        assert _lang_to_adapter_name("Nextflow") == "Nextflow"
