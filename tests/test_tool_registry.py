import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool_registry import TOOL_REGISTRY, ToolKind, install_node_tools, resolve_config


class TestToolRegistry(unittest.TestCase):
    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/vscode/node"}, clear=False)
    def test_resolve_config_uses_explicit_node_path_for_node_servers(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "node_modules" / ".bin").mkdir(parents=True)
            (base_dir / "node_modules" / ".bin" / "typescript-language-server").write_text("")
            ts_dir = base_dir / "node_modules" / "typescript-language-server"
            ts_dir.mkdir(parents=True)
            (ts_dir / "cli.mjs").write_text("")

            config = resolve_config(base_dir)
            command = config["lsp_servers"]["typescript"]["command"]

            self.assertEqual(command[0], "/vscode/node")
            self.assertTrue(command[1].endswith("cli.mjs"))
            self.assertEqual(command[2:], ["--stdio", "--log-level=2"])

    @patch("platform.system", return_value="Linux")
    def test_resolve_config_falls_back_to_embedded_nodeenv_node(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            nodeenv_bin = base_dir / "nodeenv" / "bin"
            nodeenv_bin.mkdir(parents=True)
            (nodeenv_bin / "node").write_text("")
            (nodeenv_bin / "npm").write_text("")

            (base_dir / "node_modules" / ".bin").mkdir(parents=True)
            (base_dir / "node_modules" / ".bin" / "pyright-langserver").write_text("")
            pyright_dir = base_dir / "node_modules" / "pyright" / "dist"
            pyright_dir.mkdir(parents=True)
            (pyright_dir / "langserver.index.js").write_text("")

            config = resolve_config(base_dir)
            command = config["lsp_servers"]["python"]["command"]

            self.assertEqual(command[0], str(nodeenv_bin / "node"))
            self.assertTrue(command[1].endswith("langserver.index.js"))
            self.assertEqual(command[2:], ["--stdio"])

    @patch("tool_registry.subprocess.run")
    @patch("platform.system", return_value="Linux")
    def test_install_node_tools_prefers_embedded_npm(self, mock_system, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            nodeenv_bin = base_dir / "nodeenv" / "bin"
            nodeenv_bin.mkdir(parents=True)
            (nodeenv_bin / "npm").write_text("")

            node_deps = [dep for dep in TOOL_REGISTRY if dep.kind is ToolKind.NODE]
            install_node_tools(base_dir, node_deps)

            self.assertGreaterEqual(mock_run.call_count, 2)
            first_command = mock_run.call_args_list[0].args[0]
            second_command = mock_run.call_args_list[1].args[0]
            self.assertEqual(first_command[0], str(nodeenv_bin / "npm"))
            self.assertEqual(second_command[0], str(nodeenv_bin / "npm"))
            # Verify env is passed with ELECTRON_RUN_AS_NODE
            for call in mock_run.call_args_list:
                env = call.kwargs.get("env", {})
                self.assertEqual(env.get("ELECTRON_RUN_AS_NODE"), "1")

    @patch("tool_registry.subprocess.run")
    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/vscode/node"}, clear=False)
    def test_install_node_tools_uses_bootstrapped_npm_cli(self, mock_system, mock_run):
        """When only a bootstrapped npm-cli.js exists, use [node, npm-cli.js, ...]."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            npm_cli = base_dir / "npm" / "package" / "bin" / "npm-cli.js"
            npm_cli.parent.mkdir(parents=True)
            npm_cli.write_text("")

            node_deps = [dep for dep in TOOL_REGISTRY if dep.kind is ToolKind.NODE]
            install_node_tools(base_dir, node_deps)

            self.assertGreaterEqual(mock_run.call_count, 2)
            first_command = mock_run.call_args_list[0].args[0]
            self.assertEqual(first_command[0], "/vscode/node")
            self.assertEqual(first_command[1], str(npm_cli))


if __name__ == "__main__":
    unittest.main()
