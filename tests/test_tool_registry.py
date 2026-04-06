import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool_registry import (
    TOOL_REGISTRY,
    TOOLS_REPO,
    TOOLS_TAG,
    GitHubToolSource,
    ToolKind,
    UpstreamToolSource,
    _asset_url,
    _npm_specs_fingerprint,
    _tools_fingerprint,
    download_asset,
    install_node_tools,
    needs_install,
    resolve_config,
    write_manifest,
)


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


class TestToolSource(unittest.TestCase):
    def test_asset_url_github_repo(self):
        source = GitHubToolSource(
            tag="tools-2026.01.01", repo="CodeBoarding/tools", asset_template="tokei-{platform_suffix}"
        )
        url = _asset_url(source, "tokei-linux")
        self.assertEqual(url, "https://github.com/CodeBoarding/tools/releases/download/tools-2026.01.01/tokei-linux")

    def test_asset_url_direct_upstream(self):
        source = UpstreamToolSource(
            tag="1.44.0-202501301522",
            url_template="https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}.tar.gz",
        )
        url = _asset_url(source, "ignored")
        self.assertIn("1.44.0-202501301522", url)
        self.assertTrue(url.startswith("https://download.eclipse.org/"))

    @patch("tool_registry.requests.get")
    def test_download_asset_verifies_sha256(self, mock_get):
        content = b"binary content"
        expected_hash = hashlib.sha256(content).hexdigest()

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [content]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "binary"

            # Correct hash succeeds
            result = download_asset("https://example.com/binary", dest, expected_sha256=expected_hash)
            self.assertTrue(result)
            self.assertTrue(dest.exists())

            # Wrong hash raises
            dest.unlink()
            with self.assertRaises(ValueError) as ctx:
                download_asset("https://example.com/binary", dest, expected_sha256="badhash")
            self.assertIn("SHA256 mismatch", str(ctx.exception))
            self.assertFalse(dest.exists())

    @patch("tool_registry.requests.get")
    def test_download_asset_no_hash_skips_verification(self, mock_get):
        content = b"binary content"
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [content]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "binary"
            result = download_asset("https://example.com/binary", dest)
            self.assertTrue(result)
            self.assertTrue(dest.exists())


class TestManifest(unittest.TestCase):
    def test_tools_fingerprint_includes_sources(self):
        fp = _tools_fingerprint()
        self.assertIn("tokei:", fp)
        self.assertIn(TOOLS_REPO, fp)
        self.assertIn(TOOLS_TAG, fp)

    def test_tools_fingerprint_changes_on_version_bump(self):
        fp1 = _tools_fingerprint()
        self.assertIsInstance(fp1, str)
        self.assertTrue(len(fp1) > 0)
        # The fingerprint is deterministic
        fp2 = _tools_fingerprint()
        self.assertEqual(fp1, fp2)

    @patch("tool_registry.get_servers_dir")
    def test_write_manifest_includes_tools(self, mock_servers_dir):
        with tempfile.TemporaryDirectory() as tmp:
            mock_servers_dir.return_value = Path(tmp)
            write_manifest()
            manifest = json.loads((Path(tmp) / "installed.json").read_text())
            self.assertIn("tools", manifest)
            self.assertEqual(manifest["tools"], _tools_fingerprint())

    @patch("tool_registry.has_required_tools", return_value=True)
    @patch("tool_registry._read_manifest")
    @patch("tool_registry._installed_version", return_value="1.0.0")
    def test_needs_install_triggers_on_tools_change(self, mock_version, mock_manifest, mock_tools):
        mock_manifest.return_value = {
            "version": "1.0.0",
            "npm_specs": _npm_specs_fingerprint(),
            "tools": "old-fingerprint",
        }
        self.assertTrue(needs_install())

    def test_registry_native_tools_have_source(self):
        for dep in TOOL_REGISTRY:
            if dep.kind is ToolKind.NATIVE:
                self.assertIsNotNone(dep.source, f"{dep.key} should have a source")

    def test_registry_archive_tools_have_source(self):
        for dep in TOOL_REGISTRY:
            if dep.kind is ToolKind.ARCHIVE:
                self.assertIsNotNone(dep.source, f"{dep.key} should have a source")
                self.assertTrue(dep.archive_subdir, f"{dep.key} should have archive_subdir")


if __name__ == "__main__":
    unittest.main()
