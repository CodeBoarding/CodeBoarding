import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    initialize_nodeenv_globals,
    install_embedded_node,
    install_node_tools,
    needs_install,
    node_is_acceptable,
    node_version_tuple,
    npm_specs_fingerprint,
    preferred_node_path,
    resolve_config,
    tools_fingerprint,
    write_manifest,
)


def _write_healthy_embedded_node(base_dir: Path, version: str = PINNED_NODE_VERSION) -> Path:
    """Populate ``base_dir/nodeenv/`` as a fully-healthy embedded Node install.

    Produces the exact layout that ``embedded_node_is_healthy()`` accepts:
    a non-empty, executable ``bin/node`` plus a version sentinel matching
    ``version``.  Returns the path to the fake node binary.
    """
    nodeenv_bin = base_dir / "nodeenv" / "bin"
    nodeenv_bin.mkdir(parents=True, exist_ok=True)
    node_path = nodeenv_bin / "node"
    node_path.write_text("#!/bin/sh\necho fake node\n")
    node_path.chmod(0o755)
    (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).write_text(version)
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


# These path-resolution tests predate the Node version validation that now
# lives inside preferred_node_path().  They care about plumbing — which
# candidate wins under which configuration — not about whether a given
# candidate is a runnable Node binary of the right version.  Bypassing the
# real version probe via this side_effect keeps them focused on their
# original purpose while still enforcing the "None is rejected" semantics
# that the real helper guarantees.
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

            # Look only at npm commands — ``node_is_acceptable`` is mocked so
            # it does not invoke subprocess.run, but npm_subprocess_env may
            # still trigger lookups we don't care about in this assertion.
            # The npm-related calls are the ones whose first arg ends in
            # ``/nodeenv/bin/npm``.
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
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
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
            self.assertTrue((base_dir / "nodeenv" / NODEENV_VERSION_STAMP).exists())

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
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
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
            (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).write_text(PINNED_NODE_VERSION)

            self.assertFalse(embedded_node_is_healthy(base_dir))

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

            self.assertFalse(embedded_node_is_healthy(base_dir))

    @patch("platform.system", return_value="Linux")
    def test_rejects_missing_version_sentinel(self, mock_system):
        """A ``bin/node`` without the sentinel — previous run crashed after
        dropping the binary but before stamping — must be treated as unhealthy."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _write_healthy_embedded_node(base_dir)
            (base_dir / "nodeenv" / NODEENV_VERSION_STAMP).unlink()

            self.assertFalse(embedded_node_is_healthy(base_dir))

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
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertFalse(sentinel.exists())


class TestInitializeNodeenvGlobals(unittest.TestCase):
    """Unit tests for ``initialize_nodeenv_globals`` — the helper that
    replicates the module-global setup nodeenv's own ``main()`` performs
    before calling ``create_environment``.

    This exists because nodeenv reads a module-level ``src_base_url`` global
    to build download URLs, and that global defaults to ``None``.  Calling
    ``create_environment`` without initializing it produces URLs like
    ``"None/v20.18.1/..."`` which crash with ``ValueError: unknown url type``.
    These tests guard against that regression.
    """

    def test_sets_src_base_url_to_nodejs_dist(self):
        """The common path: no --mirror, not musl/riscv -> nodejs.org."""
        import nodeenv

        # Save and restore module state so tests stay hermetic.
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
        """Lock in the exact failure mode the CI job exposed: ``src_base_url``
        must be a real URL, not ``None`` and not a string starting with
        ``None/``.  If a future nodeenv release renames the global or changes
        its semantics, this test fires loudly rather than letting the bug
        slip through again."""
        import nodeenv

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
    """Live integration test that exercises the *real* ``nodeenv`` module
    with only the HTTP download mocked.

    The earlier unit tests in ``TestInstallEmbeddedNode`` all patched
    ``nodeenv.create_environment`` wholesale, which meant they never
    exercised nodeenv's internal URL construction — and that's exactly
    where the ``src_base_url = None`` bug lived.  This test closes that
    gap: it runs the actual ``install_embedded_node`` -> ``nodeenv.main``'s
    initialization (via our helper) -> ``nodeenv.create_environment`` ->
    ``nodeenv.install_node`` -> ``nodeenv.download_node_src`` path, stopping
    only at the real HTTP call.  If any of those internal steps break on a
    future nodeenv release, this test fires instead of CI.
    """

    def test_full_install_flow_with_mocked_download(self):
        import nodeenv  # noqa: F401  — just verify it imports

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            with patch("nodeenv.urlopen", side_effect=self._fake_urlopen):
                result = install_embedded_node(base_dir)

            self.assertTrue(result, "install_embedded_node must succeed end-to-end")

            # The real nodeenv extractor should have populated these paths.
            self.assertTrue((base_dir / "nodeenv" / "bin" / "node").exists())
            self.assertTrue((base_dir / "nodeenv" / "bin" / "node").stat().st_size > 0)

            # And our version sentinel should have been written last.
            sentinel = base_dir / "nodeenv" / NODEENV_VERSION_STAMP
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text().strip(), PINNED_NODE_VERSION)

    @staticmethod
    def _fake_urlopen(*_args, **_kwargs):
        """Build an in-memory tar.gz that mimics the layout of a real Node
        release archive, so nodeenv's extractor can process it without
        hitting the network.

        The real archive is named ``node-vX.Y.Z-{linux,darwin}-{arch}.tar.gz``
        and contains a top-level ``node-vX.Y.Z-{os}-{arch}/`` directory with
        ``bin/node``, ``bin/npm``, etc.  nodeenv's extractor strips the
        top-level dir and copies the contents into ``<env_dir>/``.
        """
        import io
        import tarfile

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
    """Build a fake ``subprocess.run`` CompletedProcess result.

    Used by the version-probe tests to feed ``node_version_tuple`` a known
    stdout without actually exec'ing a real Node binary.
    """
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class TestNodeVersionProbe(unittest.TestCase):
    """Unit tests for ``node_version_tuple`` — the subprocess-based Node
    version probe that ``node_is_acceptable`` uses to enforce the minimum
    required Node major version.

    These tests exercise the parser in isolation so they remain fast and
    hermetic: they do not touch a real Node binary, do not hit the network,
    and do not depend on what version of Node (if any) is installed on the
    host running the test suite.
    """

    def test_returns_none_for_nonexistent_path(self):
        """V4 reproducer at the helper level: a missing path must be rejected
        *before* we ever spawn a subprocess, otherwise Popen raises
        FileNotFoundError on the caller's side."""
        self.assertIsNone(node_version_tuple("/definitely/does/not/exist/node"))

    def test_returns_none_for_empty_path(self):
        self.assertIsNone(node_version_tuple(""))

    def test_parses_standard_node_output(self):
        """Node prints ``v20.18.1\\n`` — the leading 'v' must be stripped and
        the three-part version parsed into a tuple."""
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
        """A node binary that fails to report its version (exit code != 0)
        should be treated as unrunnable, not parsed for partial output."""
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("", returncode=1)):
                result = node_version_tuple(tmp.name)
            self.assertIsNone(result)

    def test_returns_none_on_unparseable_output(self):
        """Garbage output (e.g. from an unrelated binary named ``node``) must
        not crash the parser — it must return None so the candidate is skipped."""
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("hello world\n")):
                self.assertIsNone(node_version_tuple(tmp.name))
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20\n")):
                self.assertIsNone(node_version_tuple(tmp.name))
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20.18\n")):
                self.assertIsNone(node_version_tuple(tmp.name))
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("va.b.c\n")):
                self.assertIsNone(node_version_tuple(tmp.name))

    def test_returns_none_on_subprocess_timeout(self):
        """A Node binary that hangs (corrupt binary, network FS stall,
        antivirus scan) must not block the caller indefinitely — the 5s
        timeout inside node_version_tuple turns a hang into a None return
        so resolution falls through to the next candidate."""
        import subprocess as sp

        with tempfile.NamedTemporaryFile() as tmp:
            with patch(
                "tool_registry.paths.subprocess.run",
                side_effect=sp.TimeoutExpired(cmd=["node"], timeout=5),
            ):
                self.assertIsNone(node_version_tuple(tmp.name))

    def test_returns_none_on_os_error(self):
        """Permission-denied, not-executable, wrong-format — any OSError from
        subprocess.run must be caught and translated to None."""
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", side_effect=OSError("permission denied")):
                self.assertIsNone(node_version_tuple(tmp.name))

    def test_sets_electron_run_as_node_env(self):
        """VS Code ships its Electron binary as the Node runtime; without
        ``ELECTRON_RUN_AS_NODE=1`` the binary would launch an editor window
        instead of responding to ``--version``.  This guards that the probe
        always sets the env var regardless of what the caller has in theirs."""
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("tool_registry.paths.subprocess.run", return_value=_fake_node_proc("v20.18.1\n")) as mock_run:
                node_version_tuple(tmp.name)
            env = mock_run.call_args.kwargs["env"]
            self.assertEqual(env["ELECTRON_RUN_AS_NODE"], "1")


class TestNodeAcceptability(unittest.TestCase):
    """Unit tests for ``node_is_acceptable`` — the minimum-version gate."""

    def test_rejects_none(self):
        self.assertFalse(node_is_acceptable(None))

    def test_rejects_empty_string(self):
        self.assertFalse(node_is_acceptable(""))

    def test_rejects_too_old_version(self):
        """Node 16 was LTS when many workstations were set up; it is below
        our current minimum of ``MINIMUM_NODE_MAJOR_VERSION`` (18), so a
        ``CODEBOARDING_NODE_PATH`` or system Node pointing at it must be
        skipped rather than used with pyright/typescript-language-server."""
        with patch("tool_registry.paths.node_version_tuple", return_value=(16, 14, 0)):
            self.assertFalse(node_is_acceptable("/usr/local/bin/node"))

    def test_rejects_ancient_version(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=(12, 0, 0)):
            self.assertFalse(node_is_acceptable("/old/node"))

    def test_accepts_minimum_version(self):
        """A Node at exactly ``MINIMUM_NODE_MAJOR_VERSION.0.0`` must be
        accepted — the constraint is ``>=``, not ``>``."""
        with patch("tool_registry.paths.node_version_tuple", return_value=(MINIMUM_NODE_MAJOR_VERSION, 0, 0)):
            self.assertTrue(node_is_acceptable("/usr/local/bin/node"))

    def test_accepts_newer_version(self):
        with patch("tool_registry.paths.node_version_tuple", return_value=(22, 11, 0)):
            self.assertTrue(node_is_acceptable("/usr/local/bin/node"))

    def test_rejects_unrunnable_binary(self):
        """If the probe returns None (hang, crash, unparseable output),
        treat as unacceptable rather than risking a downstream crash."""
        with patch("tool_registry.paths.node_version_tuple", return_value=None):
            self.assertFalse(node_is_acceptable("/broken/node"))


class TestPreferredNodePathResolution(unittest.TestCase):
    """End-to-end tests for ``preferred_node_path``'s resolution chain with
    the new version/existence validation in place.

    These tests cover:
        - V4: bogus ``CODEBOARDING_NODE_PATH`` falls through to the next candidate
        - V2/V3: too-old Node falls through to the embedded install
        - Happy path: acceptable ``CODEBOARDING_NODE_PATH`` is returned verbatim

    They use ``node_version_tuple`` mocks rather than hitting real binaries
    so they pass regardless of what the host machine has installed.
    """

    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": "/nonexistent/path/to/node"}, clear=False)
    def test_v4_nonexistent_env_var_falls_through(self, mock_system):
        """The original V4 scenario: a stale ``CODEBOARDING_NODE_PATH`` pointing
        at a deleted file must not be returned — callers would then hit
        ``FileNotFoundError: 'node'`` at Popen time, which is exactly the
        bug class this whole change set was written to prevent."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            # No embedded Node, no system Node on PATH either.
            with patch("shutil.which", return_value=None):
                result = preferred_node_path(base_dir)
            self.assertIsNone(result)

    @patch("platform.system", return_value="Linux")
    def test_v4_falls_through_to_embedded_when_env_var_bogus(self, mock_system):
        """Bogus ``CODEBOARDING_NODE_PATH`` + healthy embedded install -> the
        embedded Node is used instead of propagating the bogus path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            # Create a real file at the embedded path (so .exists() passes)
            # and mock the version probe so we don't actually exec it.
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
    def test_v2_v3_too_old_env_var_falls_through_to_embedded(self, mock_system):
        """The V2/V3 scenario: ``CODEBOARDING_NODE_PATH`` points at a real
        but too-old Node (e.g. Node 16 shipped with an LTS distro).  The
        resolver must skip it and fall through to the embedded Node v20.x
        rather than handing pyright/typescript-language-server a binary
        they will refuse to run."""
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
                    return (16, 14, 2)  # old LTS, below minimum
                if path == str(embedded):
                    return (20, 18, 1)
                return None

            with patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": str(old_node)}, clear=False):
                with patch("tool_registry.paths.node_version_tuple", side_effect=fake_version):
                    result = preferred_node_path(base_dir)

            self.assertEqual(result, str(embedded))

    @patch("platform.system", return_value="Linux")
    def test_happy_path_acceptable_env_var_wins(self, mock_system):
        """The common case: a perfectly good ``CODEBOARDING_NODE_PATH`` is
        returned verbatim without probing any other candidates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            good_node = base_dir / "good_node"
            good_node.write_text("#!/bin/sh\n")
            good_node.chmod(0o755)

            with patch.dict(os.environ, {"CODEBOARDING_NODE_PATH": str(good_node)}, clear=False):
                with patch("tool_registry.paths.node_version_tuple", return_value=(22, 11, 0)) as mock_probe:
                    result = preferred_node_path(base_dir)

            self.assertEqual(result, str(good_node))
            # Must not have probed any further candidates — short-circuit on first accept.
            probed_paths = [call.args[0] for call in mock_probe.call_args_list]
            self.assertEqual(probed_paths, [str(good_node)])

    @patch("platform.system", return_value="Linux")
    @patch.dict(os.environ, {}, clear=True)
    def test_returns_none_when_no_candidate_resolves(self, mock_system):
        """Unset env var + no embedded install + no system Node -> None.
        The caller (``ensure_node_runtime``) treats this as "must bootstrap
        the embedded runtime," and that's the correct behavior to produce
        when nothing is available."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            with patch("shutil.which", return_value=None):
                result = preferred_node_path(base_dir)
            self.assertIsNone(result)


class TestEnsureNodeOnPath(unittest.TestCase):
    """Unit tests for ``ensure_node_on_path``.

    This helper exists because Node-based LSPs (pyright,
    typescript-language-server, intelephense) spawn child processes that
    look up ``node`` via ``$PATH``.  When CodeBoarding runs against its
    embedded nodeenv and the user has no system Node, the LSP subprocess
    inherits a PATH with no ``node`` on it and those child spawns fail
    with ENOENT at analysis time.  Prepending the node binary's directory
    to the subprocess ``PATH`` fixes that without relying on the host's
    PATH being correctly configured.

    The helper operates on the ``extra_env`` dict that
    ``StaticAnalyzer.start_clients`` passes to ``LSPClient``.  Because
    ``LSPClient.start()`` merges that dict into ``os.environ.copy()`` via
    ``env.update(extra_env)`` -- which *replaces* the ``PATH`` key rather
    than merging it -- the helper must construct the full final PATH
    string, using either a PATH already set in ``extra_env`` (adapter
    intent) or the process's current ``os.environ['PATH']`` as the baseline.

    These tests pin ``os.environ['PATH']`` with ``@patch.dict`` so the
    baseline is deterministic regardless of what the host CI runner
    happens to have on its own PATH.
    """

    @patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False)
    def test_prepends_node_dir_to_os_environ_path(self):
        """The common case: ``extra_env`` is empty (adapter returned ``{}``),
        and the helper must use ``os.environ['PATH']`` as the baseline and
        prepend the node dir to it.  This is the path-producing-bug the
        fix was written to prevent: without this step, ``extra_env['PATH']``
        would be set to just the node dir, and ``env.update(extra_env)``
        inside ``LSPClient.start()`` would wipe the system PATH."""
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
        """If the adapter already set a PATH in ``extra_env`` (e.g. to make
        a vendored library visible), that value is the baseline we prepend
        to -- we do not silently replace it with ``os.environ['PATH']``."""
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
        """Calling twice must not duplicate the entry.  The helper detects
        that the node dir is already on the baseline PATH and copies the
        baseline into ``extra_env`` unchanged (so the key still exists in
        ``extra_env`` for LSPClient's ``env.update``)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "bin" / "node"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            # Seed extra_env with a PATH that already contains the node dir.
            baseline = f"{node_path.parent}{os.pathsep}/usr/bin"
            extra_env = {"PATH": baseline}

            ensure_node_on_path(command, extra_env)

            # No duplication.
            self.assertEqual(extra_env["PATH"], baseline)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_non_node_command(self):
        """Java/Go/native binaries must not have their PATH modified -- the
        heuristic is specifically 'the command is a node process launched
        from an explicit path.'"""
        with tempfile.TemporaryDirectory() as temp_dir:
            jdtls_path = Path(temp_dir) / "jdtls" / "bin" / "jdtls"
            jdtls_path.parent.mkdir(parents=True)
            jdtls_path.touch()
            command = [str(jdtls_path), "-data", "/workspace"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            # extra_env was not touched -- no PATH key was added.
            self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_bare_node_name(self):
        """If the command is a bare ``node`` (relying on PATH lookup), we
        have no directory to prepend -- and if we're here with that shape,
        upstream resolution has already decided the caller's PATH is usable.
        Don't second-guess it."""
        command = ["node", "/fake/cli.mjs", "--stdio"]
        extra_env: dict[str, str] = {}

        ensure_node_on_path(command, extra_env)

        self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_empty_command(self):
        """Defensive: an empty command list must not blow up."""
        extra_env: dict[str, str] = {}

        ensure_node_on_path([], extra_env)

        self.assertNotIn("PATH", extra_env)

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False)
    def test_no_op_for_electron_runtime(self):
        """VS Code's Electron binary is ``code`` (or ``Electron``), not
        ``node`` -- we shouldn't mistakenly prepend its directory, because
        that directory doesn't contain a standard ``node`` executable that
        grandchild processes could use.  Electron is handled via
        ``ELECTRON_RUN_AS_NODE`` elsewhere in the stack."""
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
        """On Windows the embedded node lives at ``nodeenv/Scripts/node.exe``.
        The basename check must be case-insensitive and must accept the
        ``.exe`` suffix."""
        with tempfile.TemporaryDirectory() as temp_dir:
            node_path = Path(temp_dir) / "nodeenv" / "Scripts" / "node.exe"
            node_path.parent.mkdir(parents=True)
            node_path.touch()
            command = [str(node_path), "/fake/cli.mjs", "--stdio"]
            extra_env: dict[str, str] = {}

            ensure_node_on_path(command, extra_env)

            self.assertTrue(extra_env["PATH"].startswith(str(node_path.parent)))


class TestToolSource(unittest.TestCase):
    def testasset_url_github_repo(self):
        source = GitHubToolSource(
            tag="tools-2026.01.01", repo="CodeBoarding/tools", asset_template="tokei-{platform_suffix}"
        )
        url = asset_url(source, "tokei-linux")
        self.assertEqual(url, "https://github.com/CodeBoarding/tools/releases/download/tools-2026.01.01/tokei-linux")

    def testasset_url_direct_upstream(self):
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
    def testtools_fingerprint_includes_sources(self):
        fp = tools_fingerprint()
        self.assertIn("tokei:", fp)
        self.assertIn(TOOLS_REPO, fp)
        self.assertIn(TOOLS_TAG, fp)

    def testtools_fingerprint_changes_on_version_bump(self):
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
