import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import install


class TestParseArgs(unittest.TestCase):
    def test_parse_args_defaults(self):
        with patch.object(sys, "argv", ["install.py"]):
            args = install.parse_args()
        self.assertFalse(args.auto_install_npm)

    def test_parse_args_auto_install_npm(self):
        with patch.object(sys, "argv", ["install.py", "--auto-install-npm"]):
            args = install.parse_args()
        self.assertTrue(args.auto_install_npm)


class TestResolveMissingNpm(unittest.TestCase):
    @patch("install.bootstrap_npm", return_value=True)
    def test_auto_install_mode_bootstraps_npm(self, mock_bootstrap):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_missing_npm(auto_install_npm=True, target_dir=target_dir)
        self.assertTrue(result)
        mock_bootstrap.assert_called_once_with(target_dir=target_dir)

    @patch("install.bootstrap_npm", return_value=False)
    @patch("install.is_non_interactive_mode", return_value=True)
    def test_non_interactive_falls_back_gracefully(self, mock_non_interactive, mock_bootstrap):
        """Non-interactive mode should not raise SystemExit — just return False."""
        result = install.resolve_missing_npm(auto_install_npm=False)
        self.assertFalse(result)
        mock_bootstrap.assert_called_once()

    @patch("install.bootstrap_npm", return_value=False)
    def test_auto_install_failure_degrades_gracefully(self, mock_bootstrap):
        """When auto_install is True but bootstrap fails, return False (don't crash server)."""
        result = install.resolve_missing_npm(auto_install_npm=True, target_dir=Path("/tmp/test"))
        self.assertFalse(result)


class TestResolveNpmAvailability(unittest.TestCase):
    @patch("install.resolve_missing_npm")
    @patch("install.check_npm", return_value=True)
    def test_npm_present_does_not_attempt_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_npm_availability(auto_install_npm=False, target_dir=target_dir)
        self.assertTrue(result)
        mock_check_npm.assert_called_once_with(target_dir)
        mock_resolve_missing_npm.assert_not_called()

    @patch("install.resolve_missing_npm", return_value=True)
    @patch("install.check_npm", return_value=False)
    def test_missing_npm_attempts_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_npm_availability(auto_install_npm=True, target_dir=target_dir)
        self.assertTrue(result)
        mock_check_npm.assert_called_once_with(target_dir)
        mock_resolve_missing_npm.assert_called_once_with(auto_install_npm=True, target_dir=target_dir)


class TestBootstrapNpm(unittest.TestCase):
    @patch("install.check_npm", return_value=True)
    @patch("install.requests.get")
    @patch("install.preferred_node_path", return_value="/custom/node")
    def test_bootstraps_npm_cli_when_missing(self, mock_preferred_node_path, mock_requests_get, mock_check_npm):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            target_dir = Path(temp_dir)

            metadata_response = Mock()
            metadata_response.raise_for_status.return_value = None
            metadata_response.json.return_value = {
                "dist": {"tarball": "https://registry.npmjs.org/npm/-/npm-1.0.0.tgz"}
            }
            tarball_response = Mock()
            tarball_response.raise_for_status.return_value = None
            tarball_response.content = b"fake-tarball"
            mock_requests_get.side_effect = [metadata_response, tarball_response]

            with patch("install.extract_tarball_safely") as mock_extract, patch("install.shutil.rmtree") as mock_rmtree:
                result = install.bootstrap_npm(target_dir=target_dir)
                self.assertEqual(os.environ["CODEBOARDING_NODE_PATH"], "/custom/node")

        self.assertTrue(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())
        mock_requests_get.assert_any_call("https://registry.npmjs.org/npm/latest", timeout=30)
        mock_requests_get.assert_any_call("https://registry.npmjs.org/npm/-/npm-1.0.0.tgz", timeout=60)
        mock_rmtree.assert_called_once_with(target_dir.resolve() / "npm", ignore_errors=True)
        mock_extract.assert_called_once()
        mock_check_npm.assert_called_once_with(target_dir.resolve())

    @patch("install.check_npm", return_value=True)
    @patch("install.requests.get")
    @patch("install.preferred_node_path", return_value="/custom/node")
    def test_uses_existing_bootstrapped_npm_cli(self, mock_preferred_node_path, mock_requests_get, mock_check_npm):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            target_dir = Path(temp_dir)
            npm_cli = install.bootstrapped_npm_cli_path(target_dir)
            npm_cli.parent.mkdir(parents=True, exist_ok=True)
            npm_cli.write_text("", encoding="utf-8")

            result = install.bootstrap_npm(target_dir=target_dir)

        self.assertTrue(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())
        mock_requests_get.assert_not_called()
        mock_check_npm.assert_called_once_with(target_dir.resolve())

    @patch("install.preferred_node_path", return_value=None)
    def test_returns_false_when_no_node_runtime_is_available(self, mock_preferred_node_path):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            target_dir = Path(temp_dir)
            result = install.bootstrap_npm(target_dir=target_dir)

        self.assertFalse(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())


class TestIsNonInteractiveMode(unittest.TestCase):
    @patch.dict(os.environ, {"CI": "true"})
    def test_ci_env_is_non_interactive(self):
        self.assertTrue(install.is_non_interactive_mode())

    @patch.dict(os.environ, {}, clear=True)
    @patch("install.sys")
    def test_no_tty_is_non_interactive(self, mock_sys):
        mock_sys.stdin.isatty.return_value = False
        mock_sys.frozen = False
        self.assertTrue(install.is_non_interactive_mode())

    @patch.dict(os.environ, {}, clear=True)
    @patch("install.sys")
    def test_pyinstaller_frozen_is_non_interactive(self, mock_sys):
        mock_sys.stdin.isatty.return_value = True
        mock_sys.frozen = True
        self.assertTrue(install.is_non_interactive_mode())


class TestResolveMissingVcpp(unittest.TestCase):
    @patch("install.install_vcpp_redistributable", return_value=True)
    def test_auto_install_calls_installer(self, mock_install):
        result = install.resolve_missing_vcpp(auto_install_vcpp=True)
        self.assertTrue(result)
        mock_install.assert_called_once()

    @patch("install.install_vcpp_redistributable", return_value=True)
    @patch("install.is_non_interactive_mode", return_value=True)
    def test_non_interactive_auto_installs(self, mock_non_interactive, mock_install):
        """Non-interactive mode should auto-install VC++ instead of skipping."""
        result = install.resolve_missing_vcpp(auto_install_vcpp=False)
        self.assertTrue(result)
        mock_install.assert_called_once()

    @patch("install.install_vcpp_redistributable", return_value=True)
    @patch("install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_falls_back_to_auto_install(self, mock_input, mock_non_interactive, mock_install):
        """EOFError from input() should fall back to automatic install."""
        result = install.resolve_missing_vcpp(auto_install_vcpp=False)
        self.assertTrue(result)
        mock_install.assert_called_once()


class TestResolveMissingNpmEOFError(unittest.TestCase):
    @patch("install.bootstrap_npm", return_value=True)
    @patch("install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_falls_back_to_bootstrap(self, mock_input, mock_non_interactive, mock_bootstrap):
        """EOFError from input() should fall back to bootstrap instead of crashing."""
        result = install.resolve_missing_npm(auto_install_npm=False, target_dir=Path("/tmp/test"))
        self.assertTrue(result)
        mock_bootstrap.assert_called_once()

    @patch("install.bootstrap_npm", return_value=False)
    @patch("install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_bootstrap_failure_degrades_gracefully(self, mock_input, mock_non_interactive, mock_bootstrap):
        """EOFError + bootstrap failure should degrade gracefully, not crash."""
        result = install.resolve_missing_npm(auto_install_npm=False, target_dir=Path("/tmp/test"))
        self.assertFalse(result)


class TestVerifyBinary(unittest.TestCase):
    @patch("install.subprocess.run")
    def test_ok_binary(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        result = install.verify_binary(Path("/fake/binary"))
        self.assertEqual(result, install.BinaryStatus.OK)

    @patch("install.subprocess.run")
    def test_missing_vcpp_detected(self, mock_run):
        mock_run.return_value = Mock(returncode=0xC0000135)
        result = install.verify_binary(Path("/fake/binary"))
        self.assertEqual(result, install.BinaryStatus.MISSING_VCPP)

    @patch("install.subprocess.run", side_effect=OSError("not a valid Win32 app"))
    def test_load_error(self, mock_run):
        result = install.verify_binary(Path("/fake/binary"))
        self.assertEqual(result, install.BinaryStatus.LOAD_ERROR)


class TestMainCliLock(unittest.TestCase):
    """The bare ``codeboarding-setup`` CLI entry point must hold the
    same ``.download.lock`` that ``ensure_tools`` uses, so two concurrent
    CLI invocations (or a CLI invocation racing a VSCode extension startup)
    cannot corrupt the servers directory by downloading into it in parallel.

    Testing actual multi-process locking would require subprocesses, so
    instead we assert the *structural* guarantees:

        - ``main()`` creates the lock file under the servers directory
        - ``acquire_lock`` is called on the open file descriptor
        - ``run_install`` runs while the lock is held
    """

    @patch("install.io.TextIOWrapper")
    @patch("install.write_manifest")
    @patch("install.run_install")
    @patch("install.acquire_lock")
    @patch("install.get_servers_dir")
    @patch("install.parse_args")
    def test_main_acquires_download_lock_before_run_install(
        self,
        mock_parse_args,
        mock_get_servers_dir,
        mock_acquire_lock,
        mock_run_install,
        mock_write_manifest,
        mock_text_io_wrapper,
    ):
        # parse_args returns the defaults ``main()`` would build from sys.argv.
        mock_parse_args.return_value = Mock(auto_install_npm=False, auto_install_vcpp=False)

        # main() reassigns sys.stdout/sys.stderr to io.TextIOWrapper as a
        # Windows cp1252 workaround.  Under pytest capture, that rewrap
        # explodes because pytest's stdout capture doesn't expose a real
        # ``.buffer``.  The wrapper is a side concern here (we're testing
        # the lock, not the Windows workaround), so we replace it with a
        # Mock that just swallows writes — keeps pytest's stdout untouched.
        mock_text_io_wrapper.return_value = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            servers_dir = Path(temp_dir)
            mock_get_servers_dir.return_value = servers_dir

            # Track call order so we can assert _acquire_lock fires BEFORE run_install.
            call_order: list[str] = []
            mock_acquire_lock.side_effect = lambda _fd: call_order.append("acquire")
            mock_run_install.side_effect = lambda **_kwargs: call_order.append("run_install")
            mock_write_manifest.side_effect = lambda: call_order.append("write_manifest")

            install.main()

            # Lock file exists under the servers directory — created by
            # the ``open(lock_path, "w")`` inside main().  Asserted inside
            # the tempdir's ``with`` block because the directory is
            # cleaned up when it exits.
            self.assertTrue((servers_dir / ".download.lock").exists())

        # _acquire_lock was called exactly once with a file-like object
        # (any truthy arg — the real lock fd isn't accessible here).
        mock_acquire_lock.assert_called_once()

        # And it happened before run_install, which happened before
        # write_manifest.  Both must be inside the lock's critical section.
        self.assertEqual(call_order, ["acquire", "run_install", "write_manifest"])
