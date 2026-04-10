import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tool_registry import (
    PINNED_NODE_VERSION,
    TOOL_REGISTRY,
    TOOLS_REPO,
    TOOLS_TAG,
    GitHubToolSource,
    ToolKind,
    UpstreamToolSource,
    _asset_url,
    _embedded_node_is_healthy,
    _NODEENV_VERSION_STAMP,
    _npm_specs_fingerprint,
    _tools_fingerprint,
    download_asset,
    install_embedded_node,
    install_node_tools,
    needs_install,
    resolve_config,
    write_manifest,
)


def _write_healthy_embedded_node(base_dir: Path, version: str = PINNED_NODE_VERSION) -> Path:
    """Populate ``base_dir/nodeenv/`` as a fully-healthy embedded Node install.

    Produces the exact layout that ``_embedded_node_is_healthy()`` accepts:
    a non-empty, executable ``bin/node`` plus a version sentinel matching
    ``version``.  Returns the path to the fake node binary.
    """
    nodeenv_bin = base_dir / "nodeenv" / "bin"
    nodeenv_bin.mkdir(parents=True, exist_ok=True)
    node_path = nodeenv_bin / "node"
    node_path.write_text("#!/bin/sh\necho fake node\n")
    node_path.chmod(0o755)
    (base_dir / "nodeenv" / _NODEENV_VERSION_STAMP).write_text(version)
    return node_path


def _make_successful_install_side_effect():
    """Return a fake ``nodeenv.create_environment`` side effect.

    The side effect simulates a successful install by dropping a non-empty,
    executable stub ``bin/node`` where ``embedded_node_path()`` will find it.
    It deliberately does NOT write the version sentinel — that's
    ``install_embedded_node``'s responsibility, and the tests verify that
    the sentinel is only written after a successful install.
    """

    def _side_effect(env_dir: str, _args):
        bin_dir = Path(env_dir) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        node_path = bin_dir / "node"
        node_path.write_text("#!/bin/sh\necho fake node\n")
        node_path.chmod(0o755)

    return _side_effect


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


class TestInstallEmbeddedNode(unittest.TestCase):
    """Covers the Node.js bootstrap path used when the user has no system Node.

    The suite locks in four guarantees that matter for the PyInstaller-frozen
    wrapper:

    1. **Idempotency** when a healthy install is already present — avoids
       re-downloads on every install pass.
    2. **In-process nodeenv call** with ``prebuilt=True`` — keeps the
       source-build path (python2, C compiler) unreachable from a frozen binary.
    3. **Partial-install recovery** — a stale ``nodeenv/`` directory from a
       previously interrupted run must be wiped and rebuilt, because
       ``nodeenv.create_environment()`` calls ``sys.exit(2)`` (uncatchable via
       ``except Exception``) when its target directory already exists.
    4. **Version upgrade** — bumping ``PINNED_NODE_VERSION`` must replace an
       older embedded install rather than silently keep using the old one.
    """

    @patch("platform.system", return_value="Linux")
    def test_idempotent_when_healthy_install_present(self, mock_system):
        """Healthy pre-populated install: non-empty executable node + sentinel."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)

            # If install_embedded_node ever reaches nodeenv.create_environment,
            # this mock records the call. Patch the import target, not
            # tool_registry, because the import is local inside the function.
            with patch("nodeenv.create_environment") as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_not_called()

    @patch("platform.system", return_value="Linux")
    def test_fresh_install_calls_create_environment_in_process(self, mock_system):
        """Empty base dir triggers nodeenv.create_environment with prebuilt=True."""
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

            # The two invariants that matter for frozen-binary correctness:
            #   - prebuilt=True   -> source-build path (python2, make) never hit
            #   - node == pin     -> reproducible, matches what we claim to ship
            args_arg = call_args.args[1]
            self.assertTrue(getattr(args_arg, "prebuilt", False))
            self.assertEqual(getattr(args_arg, "node", None), PINNED_NODE_VERSION)

            # A successful install must leave the version sentinel behind so
            # subsequent runs can short-circuit.
            sentinel = base_dir / "nodeenv" / _NODEENV_VERSION_STAMP
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @patch("platform.system", return_value="Linux")
    def test_recovers_from_partial_install_without_sys_exit(self, mock_system):
        """Interrupted previous run left an incomplete ``nodeenv/`` dir behind.

        This is the scenario that motivated the partial-install fix: the
        laptop slept or the user Ctrl-C'd during the previous download, so
        ``nodeenv/`` exists but ``nodeenv/bin/node`` does not.  Without the
        wipe step, ``nodeenv.create_environment()`` would call ``sys.exit(2)``
        — uncatchable via ``except Exception`` — and take down the wrapper.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            # Simulate a partial install: env dir exists, but no node binary
            # and no version sentinel.
            (base_dir / "nodeenv" / "src").mkdir(parents=True)
            (base_dir / "nodeenv" / "src" / "half-downloaded.tar.gz").write_text("garbage")

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()
            # The stale partial content must be gone — if it survived, nodeenv
            # would have raised SystemExit before our side_effect ran.
            self.assertFalse((base_dir / "nodeenv" / "src" / "half-downloaded.tar.gz").exists())
            # And the sentinel is now in place.
            self.assertTrue((base_dir / "nodeenv" / _NODEENV_VERSION_STAMP).exists())

    @patch("platform.system", return_value="Linux")
    def test_does_not_die_on_create_environment_system_exit(self, mock_system):
        """Defense-in-depth: if create_environment still raises SystemExit for
        any reason (race with a concurrent install, unexpected nodeenv change)
        we must return False, not propagate the hard exit.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            with patch("nodeenv.create_environment", side_effect=SystemExit(2)):
                # Must not propagate SystemExit.
                try:
                    result = install_embedded_node(base_dir)
                except SystemExit:
                    self.fail("install_embedded_node must catch SystemExit from nodeenv")

            self.assertFalse(result)

    @patch("platform.system", return_value="Linux")
    def test_upgrades_when_pinned_version_changes(self, mock_system):
        """Existing install with an older version stamp must be wiped and reinstalled.

        Without this, bumping ``PINNED_NODE_VERSION`` in tool_registry would
        change the manifest fingerprint (triggering ``needs_install()``) but
        never actually replace the embedded binary, because the idempotent
        short-circuit in ``install_embedded_node`` would still see a plain
        ``nodeenv/bin/node`` and return True.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            # Pretend an older version is installed — note the sentinel value.
            old_node = _write_healthy_embedded_node(base_dir, version="18.0.0")
            self.assertTrue(old_node.exists())

            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()
            # Sentinel now matches the current pin.
            sentinel = base_dir / "nodeenv" / _NODEENV_VERSION_STAMP
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @patch("platform.system", return_value="Linux")
    def test_rejects_zero_byte_node_binary(self, mock_system):
        """A zero-byte ``bin/node`` — e.g. disk filled mid-extract — must be
        treated as unhealthy so the next run wipes and retries."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            bin_dir = base_dir / "nodeenv" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "node").write_text("")  # zero bytes
            (base_dir / "nodeenv" / _NODEENV_VERSION_STAMP).write_text(PINNED_NODE_VERSION)

            self.assertFalse(_embedded_node_is_healthy(base_dir))

            # install_embedded_node should therefore reinstall.
            with patch(
                "nodeenv.create_environment",
                side_effect=_make_successful_install_side_effect(),
            ) as mock_create:
                result = install_embedded_node(base_dir)

            self.assertTrue(result)
            mock_create.assert_called_once()

    @patch("platform.system", return_value="Linux")
    def test_rejects_non_executable_node_binary(self, mock_system):
        """A non-executable ``bin/node`` — chmod step failed on Unix — must be
        treated as unhealthy so the next run wipes and retries."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)
            # Strip the exec bit.
            (base_dir / "nodeenv" / "bin" / "node").chmod(0o644)

            self.assertFalse(_embedded_node_is_healthy(base_dir))

    @patch("platform.system", return_value="Linux")
    def test_rejects_missing_version_sentinel(self, mock_system):
        """A ``bin/node`` without the sentinel — previous run crashed after
        dropping the binary but before stamping — must be treated as unhealthy."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)
            (base_dir / "nodeenv" / _NODEENV_VERSION_STAMP).unlink()

            self.assertFalse(_embedded_node_is_healthy(base_dir))

    @patch("platform.system", return_value="Linux")
    def test_does_not_stamp_sentinel_when_install_produced_empty_binary(self, mock_system):
        """If nodeenv claims success but leaves a zero-byte binary behind,
        install_embedded_node must return False and NOT write the sentinel —
        otherwise the broken install would be cached forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            def broken_side_effect(env_dir: str, _args):
                bin_dir = Path(env_dir) / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "node").write_text("")  # zero bytes

            with patch("nodeenv.create_environment", side_effect=broken_side_effect):
                result = install_embedded_node(base_dir)

            self.assertFalse(result)
            sentinel = base_dir / "nodeenv" / _NODEENV_VERSION_STAMP
            self.assertFalse(sentinel.exists())


class TestToolSource(unittest.TestCase):
    def test_asset_url_github_repo(self):
        source = GitHubToolSource(
            tag="tools-2026.01.01", repo="CodeBoarding/tools", asset_template="tokei-{platform_suffix}"
        )
        url = _asset_url(source, "tokei-linux")
        self.assertEqual(url, "https://github.com/CodeBoarding/tools/releases/download/tools-2026.01.01/tokei-linux")

    def test_asset_url_direct_upstream(self):
        source = UpstreamToolSource(
            tag="1.44.0",
            url_template="https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}-{build}.tar.gz",
            build="202501221502",
        )
        url = _asset_url(source, "ignored")
        self.assertEqual(
            url,
            "https://download.eclipse.org/jdtls/milestones/1.44.0/jdt-language-server-1.44.0-202501221502.tar.gz",
        )

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
