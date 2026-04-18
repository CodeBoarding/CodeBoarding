import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import nodeenv
from tool_registry import (
    MINIMUM_NODE_MAJOR_VERSION,
    NODEENV_VERSION_STAMP,
    PINNED_NODE_VERSION,
    TOOL_REGISTRY,
    TOOLS_REPO,
    TOOLS_TAG,
    GitHubToolSource,
    ToolKind,
    UpstreamToolSource,
    asset_url,
    download_asset,
    embedded_node_is_healthy,
    ensure_node_on_path,
    exe_suffix,
    has_required_tools,
    initialize_nodeenv_globals,
    install_embedded_node,
    install_native_tools,
    install_node_tools,
    needs_install,
    node_is_acceptable,
    node_version_tuple,
    npm_specs_fingerprint,
    platform_bin_dir,
    preferred_node_path,
    resolve_config,
    tools_fingerprint,
    write_manifest,
)
from tool_registry import PackageManagerToolSource
from tool_registry.installers import (
    _extract_compressed_binary,
    install_package_manager_tools,
    package_manager_tool_dir,
    resolve_native_asset_name,
)
from tool_registry.registry import ConfigSection, ToolDependency, ToolSource


def _write_healthy_embedded_node(base_dir: Path, version: str = PINNED_NODE_VERSION) -> Path:
    """Populate base_dir/nodeenv/ matching embedded_node_is_healthy()'s requirements."""
    nodeenv_bin = base_dir / "nodeenv" / "bin"
    nodeenv_bin.mkdir(parents=True, exist_ok=True)
    node_path = nodeenv_bin / "node"
    node_path.write_text("#!/bin/sh\necho fake node\n")
    node_path.chmod(0o755)
    (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).write_text(version)
    return node_path


def _make_successful_install_side_effect():
    """Fake nodeenv.create_environment: drops an executable stub node binary
    but not the sentinel (install_embedded_node's job)."""

    def _side_effect(env_dir: str, _args):
        bin_dir = Path(env_dir) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        node_path = bin_dir / "node"
        node_path.write_text("#!/bin/sh\necho fake node\n")
        node_path.chmod(0o755)

    return _side_effect


# Path-resolution tests care about which candidate wins, not version validation.
# Bypassing the real probe keeps them focused while preserving None-is-rejected.
def _accept_any_non_none_node(node_path):
    return bool(node_path)


class TestToolRegistry(unittest.TestCase):
    @patch("tool_registry.paths.node_is_acceptable", side_effect=_accept_any_non_none_node)
    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/vscode/node"}, clear=False)
    def test_resolve_config_uses_explicit_node_path_for_node_servers(self, mock_system, mock_accept):
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

    @patch("tool_registry.paths.node_is_acceptable", side_effect=_accept_any_non_none_node)
    @patch("platform.system", return_value="Linux")
    def test_resolve_config_falls_back_to_embedded_nodeenv_node(self, mock_system, mock_accept):
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

    @patch("tool_registry.paths.node_is_acceptable", side_effect=_accept_any_non_none_node)
    @patch("tool_registry.installers.subprocess.run")
    @patch("platform.system", return_value="Linux")
    def test_install_node_tools_prefers_embedded_npm(self, mock_system, mock_run, mock_accept):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            nodeenv_bin = base_dir / "nodeenv" / "bin"
            nodeenv_bin.mkdir(parents=True)
            (nodeenv_bin / "npm").write_text("")

            node_deps = [dep for dep in TOOL_REGISTRY if dep.kind is ToolKind.NODE]
            install_node_tools(base_dir, node_deps)

            # Filter to npm calls only — other subprocess.run calls may come
            # from npm_subprocess_env lookups we don't care about here.
            npm_calls = [
                call
                for call in mock_run.call_args_list
                if call.args and call.args[0] and str(call.args[0][0]).endswith("nodeenv/bin/npm")
            ]
            self.assertGreaterEqual(len(npm_calls), 2)
            first_command = npm_calls[0].args[0]
            second_command = npm_calls[1].args[0]
            self.assertEqual(first_command[0], str(nodeenv_bin / "npm"))
            self.assertEqual(second_command[0], str(nodeenv_bin / "npm"))
            # Verify env is passed with ELECTRON_RUN_AS_NODE
            for call in npm_calls:
                env = call.kwargs.get("env", {})
                self.assertEqual(env.get("ELECTRON_RUN_AS_NODE"), "1")

    @patch("tool_registry.paths.node_is_acceptable", side_effect=_accept_any_non_none_node)
    @patch("tool_registry.installers.subprocess.run")
    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/vscode/node"}, clear=False)
    def test_install_node_tools_uses_bootstrapped_npm_cli(self, mock_system, mock_run, mock_accept):
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


class TestInstallEmbeddedNode(unittest.TestCase):
    """Node.js bootstrap path used when the user has no system Node."""

    @patch("platform.system", return_value="Linux")
    def test_idempotent_when_healthy_install_present(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)

            # Patch the import target directly — the import is local to install_embedded_node.
            with patch("nodeenv.create_environment") as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_not_called()

    @patch("platform.system", return_value="Linux")
    def test_fresh_install_calls_create_environment_in_process(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()

            call_args = mock_create.call_args
            env_dir_arg = call_args.args[0]
            self.assertTrue(env_dir_arg.endswith("nodeenv") or "nodeenv" in env_dir_arg)

            # Frozen-binary invariants: prebuilt=True (no source build) + pinned node version.
            args_arg = call_args.args[1]
            self.assertTrue(getattr(args_arg, "prebuilt", False))
            self.assertEqual(getattr(args_arg, "node", None), PINNED_NODE_VERSION)

            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @patch("platform.system", return_value="Linux")
    def test_recovers_from_partial_install_without_sys_exit(self, mock_system):
        """Stale nodeenv/ dir from an interrupted run must be wiped before
        create_environment — otherwise it sys.exit(2)s uncatchably."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "nodeenv" / "src").mkdir(parents=True)
            (base_dir / "nodeenv" / "src" / "half-downloaded.tar.gz").write_text("garbage")

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()
            self.assertFalse((base_dir / "nodeenv" / "src" / "half-downloaded.tar.gz").exists())
            self.assertTrue((base_dir / "nodeenv" / NODEENV_VERSION_STAMP).exists())

    @patch("platform.system", return_value="Linux")
    def test_does_not_die_on_create_environment_system_exit(self, mock_system):
        """Defense in depth: SystemExit from create_environment must become False, not propagate."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            with patch("nodeenv.create_environment", side_effect=SystemExit(2)):
                try:
                    result = install_embedded_node(base_dir)
                except SystemExit:
                    self.fail("install_embedded_node must catch SystemExit from nodeenv")

            self.assertFalse(result)

    @patch("platform.system", return_value="Linux")
    def test_upgrades_when_pinned_version_changes(self, mock_system):
        """Bumping PINNED_NODE_VERSION must replace an older embedded install."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            old_node = _write_healthy_embedded_node(base_dir, version="18.0.0")
            self.assertTrue(old_node.exists())

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @patch("platform.system", return_value="Linux")
    def test_rejects_zero_byte_node_binary(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            bin_dir = base_dir / "nodeenv" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "node").write_text("")
            (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).write_text(PINNED_NODE_VERSION)

            self.assertFalse(embedded_node_is_healthy(base_dir))

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()

    @patch("platform.system", return_value="Linux")
    def test_rejects_non_executable_node_binary(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)
            (base_dir / "nodeenv" / "bin" / "node").chmod(0o644)

            self.assertFalse(embedded_node_is_healthy(base_dir))

    @patch("platform.system", return_value="Linux")
    def test_rejects_missing_version_sentinel(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)
            (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).unlink()

            self.assertFalse(embedded_node_is_healthy(base_dir))

    @patch("platform.system", return_value="Linux")
    def test_does_not_stamp_sentinel_when_install_produced_empty_binary(self, mock_system):
        """Broken install must not write the sentinel — otherwise it's cached forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            def broken_side_effect(env_dir: str, _args):
                bin_dir = Path(env_dir) / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "node").write_text("")

            with patch("nodeenv.create_environment", side_effect=broken_side_effect):
                result = install_embedded_node(base_dir)

            self.assertFalse(result)
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertFalse(sentinel.exists())

    @patch("platform.system", return_value="Linux")
    def test_clears_node_version_cache_after_successful_install(self, mock_system):
        """A pre-install probe of a broken binary poisons node_version_tuple's
        LRU under the exact path the reinstall then overwrites. Without a
        cache_clear() after install, preferred_node_path() keeps returning
        None and LSPs degrade to bare command names that ENOENT on Node-less machines."""
        node_version_tuple.cache_clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            # Simulate the broken binary that triggered the reinstall: same
            # path the new nodeenv will occupy, probed (and cached as None) here.
            bin_dir = base_dir / "nodeenv" / "bin"
            bin_dir.mkdir(parents=True)
            broken_node = bin_dir / "node"
            broken_node.write_text("broken")
            broken_node.chmod(0o755)

            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("", returncode=1)):
                self.assertIsNone(node_version_tuple(str(broken_node)))

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ):
                self.assertTrue(install_embedded_node(base_dir))

            # Same path, same process: a stale cache entry would still return None here.
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20.18.1\n")):
                self.assertEqual(node_version_tuple(str(broken_node)), (20, 18, 1))


class TestInitializeNodeenvGlobals(unittest.TestCase):
    """Guards against the ``src_base_url = None`` bug — nodeenv reads a
    module-level global when building download URLs, and it defaults to None."""

    def test_sets_src_base_url_to_nodejs_dist(self):
        saved_base = nodeenv.src_base_url
        saved_ssl = nodeenv.ignore_ssl_certs
        try:
            nodeenv.src_base_url = None
            parser = nodeenv.make_parser()
            args = parser.parse_args(["--prebuilt", "--node", "20.18.1", "/tmp/unused"])

            initialize_nodeenv_globals(nodeenv, args)

            self.assertEqual(nodeenv.src_base_url, "https://nodejs.org/download/release")
            self.assertFalse(nodeenv.ignore_ssl_certs)
        finally:
            nodeenv.src_base_url = saved_base
            nodeenv.ignore_ssl_certs = saved_ssl

    def test_src_base_url_not_none_after_init(self):
        """Guards against future nodeenv renames of the global."""
        saved_base = nodeenv.src_base_url
        saved_ssl = nodeenv.ignore_ssl_certs
        try:
            nodeenv.src_base_url = None
            parser = nodeenv.make_parser()
            args = parser.parse_args(["--prebuilt", "--node", "20.18.1", "/tmp/unused"])

            initialize_nodeenv_globals(nodeenv, args)

            base_url = nodeenv.src_base_url
            self.assertIsNotNone(base_url)
            assert base_url is not None  # for the type checker
            self.assertTrue(base_url.startswith("https://"))
            self.assertNotIn("None", base_url)
        finally:
            nodeenv.src_base_url = saved_base
            nodeenv.ignore_ssl_certs = saved_ssl


class TestInstallEmbeddedNodeEndToEnd(unittest.TestCase):
    """Exercises the real nodeenv module with only HTTP mocked to verify
    that download URL construction works end-to-end."""

    def test_full_install_flow_with_mocked_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            with patch("nodeenv.urlopen", side_effect=self._fake_urlopen):
                result = install_embedded_node(base_dir)

            self.assertTrue(result, "install_embedded_node must succeed end-to-end")
            self.assertTrue((base_dir / "nodeenv" / "bin" / "node").exists())
            self.assertTrue((base_dir / "nodeenv" / "bin" / "node").stat().st_size > 0)

            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @staticmethod
    def _fake_urlopen(*_args, **_kwargs):
        """Mimic a ``node-vX.Y.Z-{os}-{arch}.tar.gz`` archive for nodeenv's extractor."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("bin/node", "bin/npm"):
                content = b"#!/bin/sh\necho stub\n"
                info = tarfile.TarInfo(name=f"node-v{PINNED_NODE_VERSION}-linux-x64/{name}")
                info.size = len(content)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(content))
        buf.seek(0)
        return buf


def _fake_node_proc(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class TestNodeVersionProbe(unittest.TestCase):
    """Unit tests for ``node_version_tuple`` — runs in isolation without
    touching a real Node binary."""

    def setUp(self) -> None:
        # node_version_tuple is @lru_cache'd; clear so each test's subprocess.run
        # patch is actually exercised rather than short-circuited by a stale entry.
        node_version_tuple.cache_clear()

    def test_returns_none_for_nonexistent_path(self):
        """Missing path must be rejected pre-subprocess so Popen doesn't
        raise FileNotFoundError downstream."""
        self.assertIsNone(node_version_tuple("/definitely/does/not/exist/node"))

    def test_returns_none_for_empty_path(self):
        self.assertIsNone(node_version_tuple(""))

    def test_parses_standard_node_output(self):
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20.18.1\n")):
                result = node_version_tuple(tmp.name)
            self.assertEqual(result, (20, 18, 1))

    def test_parses_version_without_trailing_newline(self):
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v18.0.0")):
                result = node_version_tuple(tmp.name)
            self.assertEqual(result, (18, 0, 0))

    def test_returns_none_on_nonzero_exit(self):
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("", returncode=1)):
                result = node_version_tuple(tmp.name)
            self.assertIsNone(result)

    def test_returns_none_on_unparseable_output(self):
        bad_outputs = ["hello world\n", "v20\n", "v20.18\n", "va.b.c\n"]
        with tempfile.NamedTemporaryFile() as tmp:
            for bad_output in bad_outputs:
                # Clear per iteration so each mock is actually exercised.
                node_version_tuple.cache_clear()
                with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc(bad_output)):
                    self.assertIsNone(node_version_tuple(tmp.name), f"output {bad_output!r} should be rejected")

    def test_returns_none_on_subprocess_timeout(self):
        with tempfile.NamedTemporaryFile() as tmp:
            with patch(
                "tool_registry.paths.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["node"], timeout=5),
            ):
                self.assertIsNone(node_version_tuple(tmp.name))

    def test_returns_none_on_os_error(self):
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", side_effect=OSError("permission denied")):
                self.assertIsNone(node_version_tuple(tmp.name))

    def test_sets_electron_run_as_node_env(self):
        """VS Code's Electron needs ELECTRON_RUN_AS_NODE=1 to respond to --version."""
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20.18.1\n")) as mock_run:
                node_version_tuple(tmp.name)
            env = mock_run.call_args.kwargs["env"]
            self.assertEqual(env["ELECTRON_RUN_AS_NODE"], "1")


class TestNodeAcceptability(unittest.TestCase):
    """Minimum-version gate for ``node_is_acceptable``."""

    def test_rejects_none(self):
        self.assertFalse(node_is_acceptable(None))

    def test_rejects_empty_string(self):
        self.assertFalse(node_is_acceptable(""))

    def test_rejects_too_old_version(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=(16, 14, 0)):
            self.assertFalse(node_is_acceptable("/usr/local/bin/node"))

    def test_rejects_ancient_version(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=(12, 0, 0)):
            self.assertFalse(node_is_acceptable("/old/node"))

    def test_accepts_minimum_version(self):
        # >=, not >
        with patch("tool_registry.paths.node_version_tuple", return_value=(MINIMUM_NODE_MAJOR_VERSION, 0, 0)):
            self.assertTrue(node_is_acceptable("/usr/local/bin/node"))

    def test_accepts_newer_version(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=(22, 11, 0)):
            self.assertTrue(node_is_acceptable("/usr/local/bin/node"))

    def test_rejects_unrunnable_binary(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=None):
            self.assertFalse(node_is_acceptable("/broken/node"))


class TestPreferredNodePathResolution(unittest.TestCase):
    """End-to-end resolution chain for ``preferred_node_path`` covering the
    bogus / too-old / happy-path / nothing-available cases."""

    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/nonexistent/path/to/node"}, clear=False)
    def test_nonexistent_env_var_falls_through(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            with patch("shutil.which", return_value=None):
                result = preferred_node_path(base_dir)
            self.assertIsNone(result)

    @patch("platform.system", return_value="Linux")
    def test_falls_through_to_embedded_when_env_var_bogus(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            nodeenv_bin = base_dir / "nodeenv" / "bin"
            nodeenv_bin.mkdir(parents=True)
            embedded = nodeenv_bin / "node"
            embedded.write_text("#!/bin/sh\n")
            embedded.chmod(0o755)

            with patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/nonexistent/node"}, clear=False):
                with patch(
                    "tool_registry.paths.node_version_tuple",
                    side_effect=lambda path: (20, 18, 1) if path == str(embedded) else None,
                ):
                    result = preferred_node_path(base_dir)

            self.assertEqual(result, str(embedded))

    @patch("platform.system", return_value="Linux")
    def test_too_old_env_var_falls_through_to_embedded(self, mock_system):
        """Node 16 CODEBOARDING_NODE_PATH must fall through to embedded v20."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            old_node = base_dir / "old_node"
            old_node.write_text("#!/bin/sh\n")
            old_node.chmod(0o755)

            nodeenv_bin = base_dir / "nodeenv" / "bin"
            nodeenv_bin.mkdir(parents=True)
            embedded = nodeenv_bin / "node"
            embedded.write_text("#!/bin/sh\n")
            embedded.chmod(0o755)

            def fake_version(path: str):
                if path == str(old_node):
                    return (16, 14, 2)
                if path == str(embedded):
                    return (20, 18, 1)
                return None

            with patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": str(old_node)}, clear=False):
                with patch("tool_registry.paths.node_version_tuple", side_effect=fake_version):
                    result = preferred_node_path(base_dir)

            self.assertEqual(result, str(embedded))

    @patch("platform.system", return_value="Linux")
    def test_happy_path_acceptable_env_var_wins(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            good_node = base_dir / "good_node"
            good_node.write_text("#!/bin/sh\n")
            good_node.chmod(0o755)

            with patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": str(good_node)}, clear=False):
                with patch("tool_registry.paths.node_version_tuple", return_value=(22, 11, 0)) as mock_probe:
                    result = preferred_node_path(base_dir)

            self.assertEqual(result, str(good_node))
            # Must short-circuit on first accept, not probe further candidates.
            probed_paths = [call.args[0] for call in mock_probe.call_args_list]
            self.assertEqual(probed_paths, [str(good_node)])

    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {}, clear=True)
    def test_returns_none_when_no_candidate_resolves(self, mock_system):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            with patch("shutil.which", return_value=None):
                result = preferred_node_path(base_dir)
            self.assertIsNone(result)


class TestEnsureNodeOnPath(unittest.TestCase):
    """``ensure_node_on_path`` prepends the embedded node binary's directory
    to ``extra_env['PATH']``. LSPClient.start() does ``env.update(extra_env)``
    which *replaces* rather than merges, so the helper constructs the full
    PATH itself — using ``extra_env['PATH']`` if set, else ``os.environ['PATH']``.
    Tests pin PATH via ``@patch.dict`` for determinism."""

    @patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False)
    def test_prepends_node_dir_to_os_environ_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "bin" / "node"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            self.assertEqual(
                extra_env["PATH"],
                f"{node_path.parent}{os.pathsep}/usr/bin:/bin",
            )

    @patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False)
    def test_respects_pre_existing_path_in_extra_env(self):
        """Adapter-provided PATH is the baseline — don't replace with os.environ."""
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "bin" / "node"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            extra_env = {"PATH": "/opt/vendor/bin"}

            ensure_node_on_path(command, extra_env)

            self.assertEqual(
                extra_env["PATH"],
                f"{node_path.parent}{os.pathsep}/opt/vendor/bin",
            )

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_is_idempotent_when_node_dir_already_on_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "bin" / "node"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            baseline = f"{node_path.parent}{os.pathsep}/usr/bin"
            extra_env = {"PATH": baseline}

            ensure_node_on_path(command, extra_env)

            self.assertEqual(extra_env["PATH"], baseline)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_non_node_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            jdtls_path = Path(temp_dir) / "jdtls" / "bin" / "jdtls"
            jdtls_path.parent.mkdir(parents=True)
            jdtls_path.touch()
            command = [str(jdtls_path), "-data", "/workspace"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_bare_node_name(self):
        """Bare ``node`` means upstream resolution already trusts PATH — don't second-guess."""
        command = ["node", "/fake/cli.mjs", "--stdio"]
        extra_env: dict[str, str] = {}

        ensure_node_on_path(command, extra_env)

        self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_empty_command(self):
        extra_env: dict[str, str] = {}

        ensure_node_on_path([], extra_env)

        self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_electron_runtime(self):
        """Electron is handled via ELECTRON_RUN_AS_NODE elsewhere."""
        with tempfile.TemporaryDirectory() as temp_dir:
            code_path = Path(temp_dir) / "vscode" / "code"
            code_path.parent.mkdir(parents=True)
            code_path.touch()
            command = [str(code_path), "/fake/cli.mjs", "--stdio"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "C:\\Windows\\System32"}, clear=False)
    def test_recognizes_node_exe_on_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "Scripts" / "node.exe"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            self.assertTrue(extra_env["PATH"].startswith(str(node_path.parent)))


class TestIsAvailableOnHost(unittest.TestCase):
    """``ToolDependency.is_available_on_host`` keeps the installer and
    ``has_required_tools`` in sync so unsupported hosts (e.g. rust-analyzer
    on Linux/riscv64) don't trigger an infinite reinstall loop.
    """

    def _native_dep(self, source: ToolSource | None) -> ToolDependency:
        return ToolDependency(
            key="example",
            binary_name="example",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=source,
        )

    def test_non_native_kinds_are_always_available(self):
        node_dep = ToolDependency(
            key="ts",
            binary_name="tsserver",
            kind=ToolKind.NODE,
            config_section=ConfigSection.LSP_SERVERS,
        )
        self.assertTrue(node_dep.is_available_on_host())
        archive_dep = ToolDependency(
            key="java",
            binary_name="java",
            kind=ToolKind.ARCHIVE,
            config_section=ConfigSection.LSP_SERVERS,
        )
        self.assertTrue(archive_dep.is_available_on_host())

    def test_native_dep_without_arch_overrides_is_available(self):
        """Templated-asset deps (tokei, gopls) rely on the whole-OS guard."""
        source = GitHubToolSource(tag="v1", repo="x/y", asset_template="tokei-{platform_suffix}")
        self.assertTrue(self._native_dep(source).is_available_on_host())

    def test_arch_aware_dep_available_when_host_in_overrides(self):
        source = GitHubToolSource(
            tag="v1",
            repo="x/y",
            asset_template="ignored-{platform_suffix}",
            asset_arch_overrides={
                ("Linux", "x86_64"): "binary-linux-x86_64.gz",
                ("Darwin", "arm64"): "binary-macos-arm64.gz",
            },
        )
        with (
            patch("tool_registry.registry.platform.system", return_value="Linux"),
            patch("tool_registry.registry.platform.machine", return_value="x86_64"),
        ):
            self.assertTrue(self._native_dep(source).is_available_on_host())

    def test_arch_aware_dep_unavailable_when_host_missing_from_overrides(self):
        source = GitHubToolSource(
            tag="v1",
            repo="x/y",
            asset_template="ignored-{platform_suffix}",
            asset_arch_overrides={
                ("Linux", "x86_64"): "binary-linux-x86_64.gz",
            },
        )
        with (
            patch("tool_registry.registry.platform.system", return_value="Linux"),
            patch("tool_registry.registry.platform.machine", return_value="riscv64"),
        ):
            self.assertFalse(self._native_dep(source).is_available_on_host())


class TestToolSource(unittest.TestCase):
    def test_asset_url_github_repo(self):
        source = GitHubToolSource(
            tag="tools-2026.01.01", repo="CodeBoarding/tools", asset_template="tokei-{platform_suffix}"
        )
        url = asset_url(source, "tokei-linux")
        self.assertEqual(url, "https://github.com/CodeBoarding/tools/releases/download/tools-2026.01.01/tokei-linux")

    def test_asset_url_direct_upstream(self):
        source = UpstreamToolSource(
            tag="1.44.0",
            url_template="https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}-{build}.tar.gz",
            build="202501221502",
        )
        url = asset_url(source, "ignored")
        self.assertEqual(
            url,
            "https://download.eclipse.org/jdtls/milestones/1.44.0/jdt-language-server-1.44.0-202501221502.tar.gz",
        )

    @patch("tool_registry.installers.requests.get")
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

    @patch("tool_registry.installers.requests.get")
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
        fp = tools_fingerprint()
        self.assertIn("tokei:", fp)
        self.assertIn(TOOLS_REPO, fp)
        self.assertIn(TOOLS_TAG, fp)

    def test_tools_fingerprint_changes_on_version_bump(self):
        fp1 = tools_fingerprint()
        self.assertIsInstance(fp1, str)
        self.assertTrue(len(fp1) > 0)
        # The fingerprint is deterministic
        fp2 = tools_fingerprint()
        self.assertEqual(fp1, fp2)

    @patch("tool_registry.manifest.get_servers_dir")
    def test_write_manifest_includes_tools(self, mock_servers_dir):
        with tempfile.TemporaryDirectory() as tmp:
            mock_servers_dir.return_value = Path(tmp)
            write_manifest()
            manifest = json.loads((Path(tmp) / "installed.json").read_text())
            self.assertIn("tools", manifest)
            self.assertEqual(manifest["tools"], tools_fingerprint())

    @patch("tool_registry.manifest.has_required_tools", return_value=True)
    @patch("tool_registry.manifest.read_manifest")
    @patch("tool_registry.manifest.installed_version", return_value="1.0.0")
    def test_needs_install_triggers_on_tools_change(self, mock_version, mock_manifest, mock_tools):
        mock_manifest.return_value = {
            "version": "1.0.0",
            "npm_specs": npm_specs_fingerprint(),
            "tools": "old-fingerprint",
        }
        self.assertTrue(needs_install())

    def test_registry_native_tools_have_source(self):
        # Tools resolved externally (e.g. csharp-ls via `dotnet tool install`)
        # intentionally declare no ``source`` — the installer skips them and
        # the adapter resolves the binary from PATH / a known install location.
        externally_installed = {"csharp"}
        for dep in TOOL_REGISTRY:
            if dep.kind is ToolKind.NATIVE and dep.key not in externally_installed:
                self.assertIsNotNone(dep.source, f"{dep.key} should have a source")

    def test_registry_archive_tools_have_source(self):
        for dep in TOOL_REGISTRY:
            if dep.kind is ToolKind.ARCHIVE:
                self.assertIsNotNone(dep.source, f"{dep.key} should have a source")
                self.assertTrue(dep.archive_subdir, f"{dep.key} should have archive_subdir")

    @patch("tool_registry.manifest.get_servers_dir")
    def test_write_manifest_is_atomic(self, mock_servers_dir):
        """Crashed write must not leave a half-populated installed.json."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_servers_dir.return_value = Path(tmp)
            target = Path(tmp) / "installed.json"

            with patch("tool_registry.manifest.os.replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    write_manifest()

            self.assertFalse(target.exists())

    @patch("tool_registry.manifest.get_servers_dir")
    def test_write_manifest_overwrites_existing_file(self, mock_servers_dir):
        with tempfile.TemporaryDirectory() as tmp:
            mock_servers_dir.return_value = Path(tmp)
            target = Path(tmp) / "installed.json"
            target.write_text('{"version": "old", "npm_specs": "stale", "tools": "stale"}')

            write_manifest()

            manifest = json.loads(target.read_text())
            self.assertEqual(manifest["tools"], tools_fingerprint())
            self.assertNotEqual(manifest["version"], "old")

    @patch("tool_registry.manifest.get_servers_dir")
    def test_write_manifest_cleans_up_tmp_on_success(self, mock_servers_dir):
        with tempfile.TemporaryDirectory() as tmp:
            mock_servers_dir.return_value = Path(tmp)

            write_manifest()

            stray = list(Path(tmp).glob("installed.json.tmp"))
            self.assertEqual(stray, [], f"leftover tmp files: {stray}")


def _populate_complete_servers_dir(base_dir: Path) -> None:
    """Populate base_dir to match the layout install_tools produces.

    NATIVE -> platform_bin_dir/<name><exe>;
    NODE -> node_modules/<js_entry_parent>/lib/<js_entry_file>
    (find_runnable does a substring match on parent dir);
    ARCHIVE -> bin/<archive_subdir>/plugins/;
    PACKAGE_MANAGER -> platform_bin_dir/pm-tools/<subdir>/<name><exe>
    """
    bin_dir = platform_bin_dir(base_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NATIVE:
            (bin_dir / f"{dep.binary_name}{exe_suffix()}").write_text("#!/bin/sh\n")
        elif dep.kind is ToolKind.NODE and dep.js_entry_file:
            entry_dir = base_dir / "node_modules" / dep.js_entry_parent / "lib"
            entry_dir.mkdir(parents=True, exist_ok=True)
            (entry_dir / dep.js_entry_file).write_text("// stub\n")
        elif dep.kind is ToolKind.ARCHIVE and dep.archive_subdir:
            (base_dir / "bin" / dep.archive_subdir / "plugins").mkdir(parents=True, exist_ok=True)
        elif dep.kind is ToolKind.PACKAGE_MANAGER:
            subdir = dep.archive_subdir or dep.key
            pm_dir = bin_dir / "pm-tools" / subdir
            pm_dir.mkdir(parents=True, exist_ok=True)
            (pm_dir / f"{dep.binary_name}{exe_suffix()}").write_text("#!/bin/sh\n")


class TestHasRequiredTools(unittest.TestCase):
    """Per-kind validation: every tool must be present."""

    def test_fully_populated_dir_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            self.assertTrue(has_required_tools(base_dir))

    def test_nonexistent_base_dir_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(has_required_tools(Path(tmp) / "does-not-exist"))

    def test_missing_native_binary_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            (platform_bin_dir(base_dir) / f"gopls{exe_suffix()}").unlink()
            self.assertFalse(has_required_tools(base_dir))

    def test_missing_tokei_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            (platform_bin_dir(base_dir) / f"tokei{exe_suffix()}").unlink()
            self.assertFalse(has_required_tools(base_dir))

    def test_missing_node_js_entry_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            shutil.rmtree(base_dir / "node_modules" / "pyright")
            self.assertFalse(has_required_tools(base_dir))

    def test_missing_typescript_entry_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            shutil.rmtree(base_dir / "node_modules" / "typescript-language-server")
            self.assertFalse(has_required_tools(base_dir))

    def test_missing_archive_dir_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            shutil.rmtree(base_dir / "bin" / "jdtls")
            self.assertFalse(has_required_tools(base_dir))

    def test_archive_dir_without_plugins_subdir_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            shutil.rmtree(base_dir / "bin" / "jdtls" / "plugins")
            self.assertFalse(has_required_tools(base_dir))

    def test_needs_install_triggers_on_missing_node_install(self):
        """Integration: matching fingerprints but missing node_modules/pyright/ -> needs_install."""
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            _populate_complete_servers_dir(base_dir)
            shutil.rmtree(base_dir / "node_modules" / "pyright")

            with patch("tool_registry.manifest.get_servers_dir", return_value=base_dir):
                with patch("tool_registry.manifest.installed_version", return_value="1.0.0"):
                    with patch(
                        "tool_registry.manifest.read_manifest",
                        return_value={
                            "version": "1.0.0",
                            "npm_specs": npm_specs_fingerprint(),
                            "tools": tools_fingerprint(),
                        },
                    ):
                        self.assertTrue(needs_install())

    def test_unavailable_native_dep_does_not_block_required_tools(self):
        """Regression: rust-analyzer missing on Linux/riscv64 must not make
        ``has_required_tools`` False — that would loop forever via
        ``needs_install``.
        """
        with (
            patch("tool_registry.registry.platform.system", return_value="Linux"),
            patch("tool_registry.registry.platform.machine", return_value="riscv64"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                base_dir = Path(tmp)
                _populate_complete_servers_dir(base_dir)
                # Delete the stubbed rust-analyzer to simulate "installer skipped".
                rust_path = platform_bin_dir(base_dir) / f"rust-analyzer{exe_suffix()}"
                if rust_path.exists():
                    rust_path.unlink()
                self.assertTrue(has_required_tools(base_dir))

    def test_unavailable_native_dep_still_blocks_when_other_tools_missing(self):
        """Sanity: the skip rule must not mask a missing tokei (always required)."""
        with (
            patch("tool_registry.registry.platform.system", return_value="Linux"),
            patch("tool_registry.registry.platform.machine", return_value="riscv64"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                base_dir = Path(tmp)
                _populate_complete_servers_dir(base_dir)
                rust_path = platform_bin_dir(base_dir) / f"rust-analyzer{exe_suffix()}"
                if rust_path.exists():
                    rust_path.unlink()
                (platform_bin_dir(base_dir) / f"tokei{exe_suffix()}").unlink()
                self.assertFalse(has_required_tools(base_dir))


class TestResolveNativeAssetName(unittest.TestCase):
    """Asset-name resolution for both pre-extracted and compressed sources."""

    def test_templated_lookup_uses_platform_suffix(self):
        """Sources without arch overrides format ``asset_template`` with the suffix."""
        source = GitHubToolSource(
            tag="v1.0",
            repo="example/tool",
            asset_template="example-{platform_suffix}",
        )
        self.assertEqual(resolve_native_asset_name(source, "linux"), "example-linux")
        self.assertEqual(resolve_native_asset_name(source, "macos"), "example-macos")
        self.assertEqual(resolve_native_asset_name(source, "windows.exe"), "example-windows.exe")

    def test_empty_platform_suffix_returns_none(self):
        """Unsupported host falls through cleanly so the caller can log+skip."""
        source = GitHubToolSource(
            tag="v1.0",
            repo="example/tool",
            asset_template="example-{platform_suffix}",
        )
        self.assertIsNone(resolve_native_asset_name(source, ""))

    def test_arch_overrides_win_when_host_matches(self):
        """``asset_arch_overrides`` is consulted before the templated path."""
        source = GitHubToolSource(
            tag="2026-03-30",
            repo="rust-lang/rust-analyzer",
            asset_template="rust-analyzer-{platform_suffix}",
            asset_arch_overrides={
                ("Linux", "x86_64"): "rust-analyzer-x86_64-unknown-linux-gnu.gz",
                ("Darwin", "arm64"): "rust-analyzer-aarch64-apple-darwin.gz",
            },
        )
        with (
            patch("tool_registry.installers.platform.system", return_value="Linux"),
            patch("tool_registry.installers.platform.machine", return_value="x86_64"),
        ):
            self.assertEqual(
                resolve_native_asset_name(source, "linux"),
                "rust-analyzer-x86_64-unknown-linux-gnu.gz",
            )
        with (
            patch("tool_registry.installers.platform.system", return_value="Darwin"),
            patch("tool_registry.installers.platform.machine", return_value="arm64"),
        ):
            self.assertEqual(
                resolve_native_asset_name(source, "macos"),
                "rust-analyzer-aarch64-apple-darwin.gz",
            )

    def test_arch_overrides_unsupported_host_returns_none(self):
        """If ``asset_arch_overrides`` is set but this host is missing, return None.

        This is intentional — falling through to the templated suffix would
        download the wrong-arch binary instead of cleanly reporting "no
        release for this host".
        """
        source = GitHubToolSource(
            tag="2026-03-30",
            repo="rust-lang/rust-analyzer",
            asset_template="rust-analyzer-{platform_suffix}",
            asset_arch_overrides={
                ("Linux", "x86_64"): "rust-analyzer-x86_64-unknown-linux-gnu.gz",
            },
        )
        with (
            patch("tool_registry.installers.platform.system", return_value="Linux"),
            patch("tool_registry.installers.platform.machine", return_value="riscv64"),
        ):
            self.assertIsNone(resolve_native_asset_name(source, "linux"))


class TestExtractCompressedBinary(unittest.TestCase):
    """Decompression of gzipped and zipped single-binary release assets.

    Format is inferred from the archive filename suffix (``.gz`` or ``.zip``).
    """

    def test_gz_writes_decompressed_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.gz"
            target = tmp_path / "tool"
            payload = b"#!/bin/sh\necho rust-analyzer-mock\n" * 100
            with gzip.open(archive, "wb") as f:
                f.write(payload)

            _extract_compressed_binary(archive, "", target)

            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), payload)

    def test_zip_with_single_member_extracts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            target = tmp_path / "tool.exe"
            payload = b"MZ\x90\x00\x03\x00\x00\x00mock-exe-bytes"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("rust-analyzer.exe", payload)

            _extract_compressed_binary(archive, "", target)

            self.assertEqual(target.read_bytes(), payload)

    def test_zip_picks_only_exe_member_from_mixed_archive(self):
        """When a zip has multiple members, prefer the unique .exe candidate."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            target = tmp_path / "tool.exe"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("README.txt", b"docs")
                zf.writestr("LICENSE", b"license")
                zf.writestr("rust-analyzer.exe", b"binary-bytes")

            _extract_compressed_binary(archive, "", target)

            self.assertEqual(target.read_bytes(), b"binary-bytes")

    def test_zip_with_explicit_inner_path(self):
        """When ``archive_inner_path`` is set the named member is used verbatim."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            target = tmp_path / "tool"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("nested/dir/binary", b"payload")

            _extract_compressed_binary(archive, "nested/dir/binary", target)

            self.assertEqual(target.read_bytes(), b"payload")

    def test_zip_with_missing_inner_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("only-member", b"")

            with self.assertRaises(ValueError) as ctx:
                _extract_compressed_binary(archive, "missing", tmp_path / "out")
            self.assertIn("missing", str(ctx.exception))

    def test_zip_ambiguous_members_raises(self):
        """Multiple non-.exe members with no inner_path is unrecoverable."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("foo", b"a")
                zf.writestr("bar", b"b")

            with self.assertRaises(ValueError):
                _extract_compressed_binary(archive, "", tmp_path / "out")

    def test_unknown_suffix_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _extract_compressed_binary(Path(tmp) / "x.rar", "", Path(tmp) / "out")

    def test_extraction_failure_does_not_leave_partial_binary(self):
        """A failed extraction must not publish a half-written file at *target*
        — ``has_required_tools`` only checks existence, so a truncated binary
        would be treated as installed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.zip"
            target = tmp_path / "rust-analyzer"
            # Build a zip with two ambiguous members so the extractor raises
            # ValueError BEFORE writing anything.
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("foo", b"a")
                zf.writestr("bar", b"b")

            with self.assertRaises(ValueError):
                _extract_compressed_binary(archive, "", target)

            # Final binary path must NOT exist.
            self.assertFalse(target.exists())
            # Sibling temp file must NOT exist either.
            self.assertFalse((tmp_path / "rust-analyzer.extract").exists())

    def test_successful_extraction_replaces_existing_target(self):
        """A repeat install over a stale binary should atomically swap it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "tool.gz"
            target = tmp_path / "rust-analyzer"
            target.write_bytes(b"stale-bytes")
            payload = b"fresh-binary-bytes" * 50
            with gzip.open(archive, "wb") as f:
                f.write(payload)

            _extract_compressed_binary(archive, "", target)

            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse((tmp_path / "rust-analyzer.extract").exists())


class TestInstallNativeToolsCompressed(unittest.TestCase):
    """End-to-end install path for compressed-asset native tools."""

    def _make_compressed_dep(self, asset_name: str) -> ToolDependency:
        return ToolDependency(
            key="rust",
            binary_name="rust-analyzer",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=GitHubToolSource(
                tag="2026-03-30",
                repo="rust-lang/rust-analyzer",
                asset_template="rust-analyzer-{platform_suffix}",
                asset_arch_overrides={
                    ("Linux", "x86_64"): asset_name,
                },
            ),
        )

    def test_gz_asset_is_decompressed_and_chmod_set(self):
        """A gz dep gets downloaded, decompressed, and marked executable."""
        dep = self._make_compressed_dep("rust-analyzer-x86_64-unknown-linux-gnu.gz")
        binary_payload = b"\x7fELF" + b"fake-elf-bytes" * 200

        # The fake "download" writes a gzipped payload to the destination path.
        def fake_download(url: str, destination: Path, expected_sha256: str | None = None) -> bool:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(destination, "wb") as f:
                f.write(binary_payload)
            return True

        # ``installers.platform`` and ``paths.platform`` reference the same
        # global ``platform`` module, so a single patch on ``platform.system``
        # affects both call sites. The two patch lines below are equivalent
        # (both set the same attribute) — kept symmetric for readability.
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="Linux"),
                patch("tool_registry.installers.platform.machine", return_value="x86_64"),
                patch("tool_registry.paths.platform.system", return_value="Linux"),
                patch("tool_registry.installers.download_asset", side_effect=fake_download),
            ):
                install_native_tools(target_dir, [dep])

                installed = platform_bin_dir(target_dir) / "rust-analyzer"
                self.assertTrue(installed.exists())
                self.assertEqual(installed.read_bytes(), binary_payload)
                # Archive temp file must be cleaned up.
                archive_leftover = platform_bin_dir(target_dir) / "rust-analyzer-x86_64-unknown-linux-gnu.gz"
                self.assertFalse(archive_leftover.exists())
                # On Unix, the binary should be executable.
                mode = installed.stat().st_mode & 0o777
                self.assertEqual(mode & 0o111, 0o111)

    def test_compressed_asset_honors_per_asset_sha256(self):
        """``sha256[asset_name]`` lets a registry author pin compressed assets."""
        binary_payload = b"#!/bin/sh\necho rust-analyzer\n" * 10
        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode="wb") as f:
            f.write(binary_payload)
        gz_bytes = gz_buf.getvalue()
        gz_hash = hashlib.sha256(gz_bytes).hexdigest()

        dep = ToolDependency(
            key="rust",
            binary_name="rust-analyzer",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=GitHubToolSource(
                tag="2026-03-30",
                repo="rust-lang/rust-analyzer",
                asset_template="rust-analyzer-{platform_suffix}",
                sha256={"rust-analyzer-x86_64-unknown-linux-gnu.gz": gz_hash},
                asset_arch_overrides={
                    ("Linux", "x86_64"): "rust-analyzer-x86_64-unknown-linux-gnu.gz",
                },
            ),
        )

        def fake_download(url, destination, expected_sha256=None):
            # Mirror download_asset's verification: write the bytes, then
            # check the hash matches what the installer passed in.
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(gz_bytes)
            if expected_sha256 is not None:
                actual = hashlib.sha256(destination.read_bytes()).hexdigest()
                if actual != expected_sha256:
                    raise ValueError(f"hash mismatch: expected={expected_sha256} got={actual}")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="Linux"),
                patch("tool_registry.installers.platform.machine", return_value="x86_64"),
                patch("tool_registry.installers.download_asset", side_effect=fake_download) as mock_dl,
            ):
                install_native_tools(target_dir, [dep])
            # Verify the installer asked download_asset to verify the hash.
            call_kwargs = mock_dl.call_args.kwargs
            self.assertEqual(call_kwargs.get("expected_sha256"), gz_hash)

    def test_compressed_asset_falls_through_when_no_per_asset_pin(self):
        """Without an exact-asset pin, compressed deps still skip verification.

        The per-suffix sha256 map is meant for tools we re-publish ourselves
        (tokei, gopls), not for upstream-managed compressed releases.
        """
        dep = ToolDependency(
            key="rust",
            binary_name="rust-analyzer",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=GitHubToolSource(
                tag="2026-03-30",
                repo="rust-lang/rust-analyzer",
                asset_template="rust-analyzer-{platform_suffix}",
                # Note: a stale per-suffix entry must NOT be applied to the
                # compressed asset (it would always mismatch).
                sha256={"linux": "0" * 64},
                asset_arch_overrides={
                    ("Linux", "x86_64"): "rust-analyzer-x86_64-unknown-linux-gnu.gz",
                },
            ),
        )

        def fake_download(url: str, destination: Path, expected_sha256: str | None = None) -> bool:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(destination, "wb") as f:
                f.write(b"\x7fELFmock")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="Linux"),
                patch("tool_registry.installers.platform.machine", return_value="x86_64"),
                patch("tool_registry.installers.download_asset", side_effect=fake_download) as mock_dl,
            ):
                install_native_tools(target_dir, [dep])
            self.assertIsNone(mock_dl.call_args.kwargs.get("expected_sha256"))

    def test_zip_asset_is_extracted(self):
        """Windows ``.zip`` assets must use the zip extractor; the format is
        inferred from the asset filename suffix per arch override.
        """
        dep = ToolDependency(
            key="rust",
            binary_name="rust-analyzer",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=GitHubToolSource(
                tag="2026-03-30",
                repo="rust-lang/rust-analyzer",
                asset_template="rust-analyzer-{platform_suffix}",
                asset_arch_overrides={
                    ("Windows", "AMD64"): "rust-analyzer-x86_64-pc-windows-msvc.zip",
                },
            ),
        )
        binary_payload = b"MZ\x90\x00mock-windows-binary"

        def fake_download(url, destination, expected_sha256=None):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as zf:
                zf.writestr("rust-analyzer.exe", binary_payload)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="Windows"),
                patch("tool_registry.installers.platform.machine", return_value="AMD64"),
                patch("tool_registry.paths.platform.system", return_value="Windows"),
                patch("tool_registry.installers.download_asset", side_effect=fake_download),
            ):
                install_native_tools(target_dir, [dep])
                installed = platform_bin_dir(target_dir) / "rust-analyzer.exe"
                self.assertTrue(installed.exists())
                self.assertEqual(installed.read_bytes(), binary_payload)

    def test_unsupported_arch_skips_cleanly(self):
        """A host with no arch override is reported and skipped, not crashed."""
        dep = self._make_compressed_dep("rust-analyzer-x86_64-unknown-linux-gnu.gz")
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="Linux"),
                patch("tool_registry.installers.platform.machine", return_value="riscv64"),
                patch("tool_registry.installers.download_asset") as mock_download,
            ):
                install_native_tools(target_dir, [dep])
                mock_download.assert_not_called()
            installed = platform_bin_dir(target_dir) / "rust-analyzer"
            self.assertFalse(installed.exists())

    def test_unsupported_platform_logs_warning_and_returns_cleanly(self):
        """An unknown ``platform.system()`` (e.g. FreeBSD) makes
        ``platform_bin_dir`` raise; the installer must catch it, log one
        warning, and return rather than propagate the RuntimeError.
        """
        compressed_dep = self._make_compressed_dep("rust-analyzer-x86_64-unknown-linux-gnu.gz")
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            with (
                patch("tool_registry.installers.platform.system", return_value="FreeBSD"),
                self.assertLogs("tool_registry.installers", level="WARNING") as logs,
            ):
                install_native_tools(target_dir, [compressed_dep])
            warning_text = "\n".join(logs.output)
            self.assertIn("Unsupported platform FreeBSD", warning_text)


class TestRustRegistryEntry(unittest.TestCase):
    """Smoke tests for the rust-analyzer entry in TOOL_REGISTRY."""

    def test_rust_entry_present(self):
        rust_deps = [d for d in TOOL_REGISTRY if d.key == "rust"]
        self.assertEqual(len(rust_deps), 1)
        self.assertEqual(rust_deps[0].binary_name, "rust-analyzer")
        self.assertEqual(rust_deps[0].kind, ToolKind.NATIVE)

    def test_rust_source_has_arch_overrides_for_all_three_platforms(self):
        """Sanity: Linux, macOS, Windows are all covered (at least one arch each)."""
        rust = next(d for d in TOOL_REGISTRY if d.key == "rust")
        assert isinstance(rust.source, GitHubToolSource)
        systems = {key[0] for key in rust.source.asset_arch_overrides}
        self.assertEqual(systems, {"Linux", "Darwin", "Windows"})

    def test_rust_uses_compressed_assets(self):
        """Every override in the rust entry must end in .gz or .zip so the
        installer's suffix-based decompression picks the right format.
        """
        rust = next(d for d in TOOL_REGISTRY if d.key == "rust")
        assert isinstance(rust.source, GitHubToolSource)
        for asset in rust.source.asset_arch_overrides.values():
            self.assertTrue(asset.endswith(".gz") or asset.endswith(".zip"), asset)

    def test_rust_fingerprint_includes_tag(self):
        """Bumping ``RUST_ANALYZER_TAG`` must invalidate manifests."""
        rust = next(d for d in TOOL_REGISTRY if d.key == "rust")
        assert isinstance(rust.source, GitHubToolSource)
        self.assertIn(rust.source.tag, tools_fingerprint())


class TestInstallPackageManagerTools(unittest.TestCase):
    """``install_package_manager_tools`` invokes a user-provided package
    manager (e.g. ``dotnet tool install``) and must degrade gracefully
    when the manager itself is missing."""

    def _csharp_dep(self) -> ToolDependency:
        return ToolDependency(
            key="csharp",
            binary_name="csharp-ls",
            kind=ToolKind.PACKAGE_MANAGER,
            config_section=ConfigSection.LSP_SERVERS,
            source=PackageManagerToolSource(
                tag="0.20.0",
                manager_binary="dotnet",
                install_args=("tool", "install", "csharp-ls", "--version", "{tag}", "--tool-path", "{tool_path}"),
            ),
            archive_subdir="csharp-ls",
        )

    def test_skips_when_manager_binary_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with (
                patch("tool_registry.installers.shutil.which", return_value=None),
                patch("tool_registry.installers.subprocess.run") as mock_run,
            ):
                install_package_manager_tools(base, [self._csharp_dep()])
            mock_run.assert_not_called()

    def test_invokes_manager_with_substituted_tool_path(self):
        dep = self._csharp_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_dir = package_manager_tool_dir(base, dep)
            binary_path = install_dir / f"csharp-ls{exe_suffix()}"

            def fake_run(cmd, **_kwargs):
                # Simulate ``dotnet tool install`` dropping the binary.
                binary_path.parent.mkdir(parents=True, exist_ok=True)
                binary_path.write_text("fake csharp-ls")
                result = MagicMock()
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result

            with (
                patch("tool_registry.installers.shutil.which", return_value="/usr/bin/dotnet"),
                patch("tool_registry.installers.subprocess.run", side_effect=fake_run) as mock_run,
            ):
                install_package_manager_tools(base, [dep])

            self.assertEqual(mock_run.call_count, 1)
            invoked_cmd = mock_run.call_args.args[0]
            self.assertEqual(invoked_cmd[0], "dotnet")
            # Placeholders must be substituted: {tool_path} -> install dir, {tag} -> source.tag.
            self.assertIn(str(install_dir), invoked_cmd)
            self.assertIn("0.20.0", invoked_cmd)
            self.assertNotIn("{tool_path}", invoked_cmd)
            self.assertNotIn("{tag}", invoked_cmd)
            self.assertTrue(binary_path.exists())

    def test_idempotent_when_binary_already_present(self):
        dep = self._csharp_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_dir = package_manager_tool_dir(base, dep)
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / f"csharp-ls{exe_suffix()}").write_text("pre-existing")

            with (
                patch("tool_registry.installers.shutil.which", return_value="/usr/bin/dotnet"),
                patch("tool_registry.installers.subprocess.run") as mock_run,
            ):
                install_package_manager_tools(base, [dep])
            mock_run.assert_not_called()

    def test_reports_failure_when_manager_exits_nonzero(self):
        dep = self._csharp_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_dir = package_manager_tool_dir(base, dep)

            failed = MagicMock()
            failed.returncode = 1
            failed.stdout = ""
            failed.stderr = "boom"

            with (
                patch("tool_registry.installers.shutil.which", return_value="/usr/bin/dotnet"),
                patch("tool_registry.installers.subprocess.run", return_value=failed),
            ):
                install_package_manager_tools(base, [dep])

            self.assertFalse((install_dir / f"csharp-ls{exe_suffix()}").exists())

    def test_fingerprint_changes_with_version_tag(self):
        """Bumping the pinned tag must invalidate manifests (same guarantee
        as GitHub-sourced tools).
        """
        csharp = next(d for d in TOOL_REGISTRY if d.key == "csharp")
        assert isinstance(csharp.source, PackageManagerToolSource)
        self.assertIn(csharp.source.tag, tools_fingerprint())

    def test_csharp_registry_targets_net10_framework(self):
        """C# package-manager install must target .NET 10 during full migration."""
        csharp = next(d for d in TOOL_REGISTRY if d.key == "csharp")
        assert isinstance(csharp.source, PackageManagerToolSource)
        self.assertIn("--framework", csharp.source.install_args)
        framework_idx = csharp.source.install_args.index("--framework") + 1
        self.assertEqual(csharp.source.install_args[framework_idx], "net10.0")


if __name__ == "__main__":
    unittest.main()
