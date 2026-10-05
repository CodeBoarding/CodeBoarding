import json
from unittest.mock import Mock, patch

import pytest

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import _create_engine_configs
from static_analyzer.config import LANGUAGE_ID_BY_SUFFIX, SOURCE_EXTENSION_TO_LANGUAGE, Language
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.scanner import ProjectScanner
from tool_registry.manifest import resolve_config
from vscode_constants import VSCODE_CONFIG


@pytest.mark.parametrize("scanner_name", ["BASH", "Shell"])
def test_bash_scanner_creates_engine_and_discovers_only_supported_scripts(tmp_path, scanner_name):
    for name in ("build.sh", "helpers.bash", "other.py", "extensionless", "ignored.sh"):
        (tmp_path / name).write_text("#!/bin/bash\nhello() { echo hello; }\n")
    (tmp_path / ".gitignore").write_text("ignored.sh\n")
    output = {scanner_name: {"code": 2, "reports": [{"name": "build.sh"}]}, "Total": {"code": 2}}
    with (
        patch("static_analyzer.scanner.get_config", side_effect=VSCODE_CONFIG.__getitem__),
        patch("static_analyzer.scanner.subprocess.run", return_value=Mock(stdout=json.dumps(output))),
        patch("static_analyzer.scanner.track_tech_stack"),
    ):
        languages = ProjectScanner(tmp_path).scan()
    assert len(languages) == 1
    assert languages[0].get_server_parameters() == ["bash-language-server", "start"]
    ignore = RepoIgnoreManager(tmp_path)
    configs = _create_engine_configs(languages, tmp_path, ignore)
    assert len(configs) == 1
    adapter = configs[0].adapter
    assert adapter.language_enum == Language.BASH
    assert adapter.language_id == "bash"
    assert adapter.discover_source_files(tmp_path, ignore) == [tmp_path / "build.sh", tmp_path / "helpers.bash"]
    for suffix in (".sh", ".bash"):
        assert LANGUAGE_ID_BY_SUFFIX[suffix] == "bash"
        assert SOURCE_EXTENSION_TO_LANGUAGE[suffix] == Language.BASH


def test_bash_resolves_installed_node_entrypoint_and_preserves_start_argument(tmp_path):
    entry = tmp_path / "node_modules/bash-language-server/out/cli.js"
    entry.parent.mkdir(parents=True)
    entry.touch()
    binary_dir = tmp_path / "node_modules/.bin"
    binary_dir.mkdir()
    (binary_dir / "bash-language-server").touch()
    (binary_dir / "bash-language-server.cmd").touch()
    with patch("tool_registry.manifest.preferred_node_path", return_value="node"):
        config = resolve_config(tmp_path)
    command = config["lsp_servers"]["bash"]["command"]
    assert command == ["node", str(entry), "start"]
    adapter = get_adapter("Bash")
    with patch("static_analyzer.engine.language_adapter.get_config", return_value=config["lsp_servers"]):
        assert adapter.get_lsp_command(tmp_path) == command
    assert adapter.lsp_command == ["bash-language-server", "start"]
