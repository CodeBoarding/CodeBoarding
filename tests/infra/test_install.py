import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, call, patch

from infra import install


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
    @patch("infra.install.bootstrap_npm", return_value=True)
    def test_auto_install_mode_bootstraps_npm(self, mock_bootstrap):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_missing_npm(auto_install_npm=True, target_dir=target_dir)
        self.assertTrue(result)
        mock_bootstrap.assert_called_once_with(target_dir=target_dir)

    @patch("infra.install.bootstrap_npm", return_value=False)
    @patch("infra.install.is_non_interactive_mode", return_value=True)
    def test_non_interactive_falls_back_gracefully(self, mock_non_interactive, mock_bootstrap):
        """Non-interactive mode should not raise SystemExit — just return False."""
        result = install.resolve_missing_npm(auto_install_npm=False)
        self.assertFalse(result)
        mock_bootstrap.assert_called_once()

    @patch("infra.install.bootstrap_npm", return_value=False)
    def test_auto_install_failure_degrades_gracefully(self, mock_bootstrap):
        """When auto_install is True but bootstrap fails, return False (don't crash server)."""
        result = install.resolve_missing_npm(auto_install_npm=True, target_dir=Path("/tmp/test"))
        self.assertFalse(result)

    @patch("builtins.input", return_value="n")
    @patch("infra.install.is_non_interactive_mode", return_value=False)
    def test_declining_npm_logs_an_error_and_exits(self, mock_non_interactive: Mock, mock_input: Mock) -> None:
        with self.assertLogs("infra.install", level="ERROR") as logs, self.assertRaises(SystemExit) as raised:
            install.resolve_missing_npm(auto_install_npm=False)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("npm is required", logs.output[0])


class TestMain(unittest.TestCase):
    @patch("infra.install.write_manifest")
    @patch("infra.install.run_install")
    @patch("infra.install.acquire_lock")
    @patch("infra.install.setup_logging")
    def test_setup_configures_logging_before_reporting_progress(
        self, mock_setup_logging: Mock, mock_acquire_lock: Mock, mock_run_install: Mock, mock_write_manifest: Mock
    ) -> None:
        """Why: without a configured handler the INFO-level progress of setup would be dropped."""
        events = Mock()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("infra.install.get_servers_dir", return_value=Path(tmp)),
            patch.object(sys, "argv", ["codeboarding-setup", "--auto-install-npm"]),
            patch.object(sys, "stdout", io.TextIOWrapper(io.BytesIO())),
            patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())),
            patch.object(install.logger, "info", wraps=install.logger.info) as mock_info,
            self.assertLogs("infra.install", level="INFO") as logs,
        ):
            events.attach_mock(mock_setup_logging, "setup_logging")
            events.attach_mock(mock_info, "info")
            install.main()

        mock_setup_logging.assert_called_once_with()
        mock_run_install.assert_called_once_with(auto_install_npm=True, auto_install_vcpp=False)
        self.assertEqual(events.mock_calls[:2], [call.setup_logging(), call.info("CodeBoarding Setup")])
        self.assertIn("Setup complete!", "\n".join(logs.output))


class TestResolveNpmAvailability(unittest.TestCase):
    @patch("infra.install.resolve_missing_npm")
    @patch("infra.install.check_npm", return_value=True)
    def test_npm_present_does_not_attempt_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_npm_availability(auto_install_npm=False, target_dir=target_dir)
        self.assertTrue(result)
        mock_check_npm.assert_called_once_with(target_dir)
        mock_resolve_missing_npm.assert_not_called()

    @patch("infra.install.resolve_missing_npm", return_value=True)
    @patch("infra.install.check_npm", return_value=False)
    def test_missing_npm_attempts_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_npm_availability(auto_install_npm=True, target_dir=target_dir)
        self.assertTrue(result)
        mock_check_npm.assert_called_once_with(target_dir)
        mock_resolve_missing_npm.assert_called_once_with(auto_install_npm=True, target_dir=target_dir)


class TestRustToolchainCheck(unittest.TestCase):
    @patch("infra.install.shutil.which", return_value=None)
    def test_missing_cargo_disables_rust_support(self, mock_which):
        ok, reason = install.check_rust_toolchain()

        self.assertFalse(ok)
        assert reason is not None
        self.assertIn("cargo not found", reason)

    @patch("infra.install.subprocess.run")
    @patch("infra.install.shutil.which", return_value="/usr/local/bin/cargo")
    def test_broken_cargo_disables_rust_support(self, mock_which, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(134, ["cargo", "--version"], stderr="dyld missing")

        ok, reason = install.check_rust_toolchain()

        self.assertFalse(ok)
        assert reason is not None
        self.assertIn("cargo failed to run", reason)
        self.assertIn("dyld missing", reason)

    @patch("infra.install.subprocess.run")
    @patch("infra.install.shutil.which", return_value="/usr/local/bin/cargo")
    def test_working_cargo_enables_rust_support(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(["cargo", "--version"], 0, stdout="cargo 1.0.0")

        ok, reason = install.check_rust_toolchain()

        self.assertTrue(ok)
        self.assertIsNone(reason)


class TestBootstrapNpm(unittest.TestCase):
    @patch("infra.install.check_npm", return_value=True)
    @patch("infra.install.requests.get")
    @patch("infra.install.preferred_node_path", return_value="/custom/node")
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

            with (
                patch("infra.install.extract_tarball_safely") as mock_extract,
                patch("infra.install.shutil.rmtree") as mock_rmtree,
            ):
                result = install.bootstrap_npm(target_dir=target_dir)
                self.assertEqual(os.environ["CODEBOARDING_NODE_PATH"], "/custom/node")

        self.assertTrue(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())
        mock_requests_get.assert_any_call("https://registry.npmjs.org/npm/latest", timeout=30)
        mock_requests_get.assert_any_call("https://registry.npmjs.org/npm/-/npm-1.0.0.tgz", timeout=60)
        mock_rmtree.assert_called_once_with(target_dir.resolve() / "npm", ignore_errors=True)
        mock_extract.assert_called_once()
        mock_check_npm.assert_called_once_with(target_dir.resolve())

    @patch("infra.install.check_npm", return_value=True)
    @patch("infra.install.requests.get")
    @patch("infra.install.preferred_node_path", return_value="/custom/node")
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

    @patch("infra.install.preferred_node_path", return_value=None)
    def test_returns_false_when_no_node_runtime_is_available(self, mock_preferred_node_path):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            target_dir = Path(temp_dir)
            result = install.bootstrap_npm(target_dir=target_dir)

        self.assertFalse(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())

    @patch("infra.install.check_npm")
    @patch("infra.install.requests.get", side_effect=install.requests.ConnectionError("offline"))
    @patch("infra.install.preferred_node_path", return_value="/custom/node")
    def test_failed_download_logs_a_warning_with_the_traceback(
        self, mock_preferred_node_path: Mock, mock_requests_get: Mock, mock_check_npm: Mock
    ) -> None:
        """Why: an offline setup carries on, but the handled error must keep its traceback for diagnosis."""
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(os.environ, {}, clear=True),
            self.assertLogs("infra.install", level="WARNING") as logs,
        ):
            result = install.bootstrap_npm(target_dir=Path(temp_dir))

        self.assertFalse(result)
        mock_check_npm.assert_not_called()
        [failure] = [record for record in logs.records if "offline" in record.getMessage()]
        self.assertIsNotNone(failure.exc_info)


class TestLanguageSupportSummary(unittest.TestCase):
    def test_unavailable_languages_and_reasons_are_warnings(self) -> None:
        """Why: a WARNING-filtered setup log must still say which languages are unavailable and why."""
        checks = [
            install.LanguageSupportCheck(language="Python", paths=[], fallback_available=True),
            install.LanguageSupportCheck(language="Go", paths=[], reason_if_binary_missing="gopls not found"),
        ]
        with (
            patch("infra.install._language_checks_from_registry", return_value=checks),
            self.assertLogs("infra.install", level="WARNING") as logs,
        ):
            install.print_language_support_summary(npm_available=True, target_dir=Path("/tmp/unused"))

        self.assertEqual(
            logs.output, ["WARNING:infra.install:  - Go: no", "WARNING:infra.install:    reason: gopls not found"]
        )


class TestIsNonInteractiveMode(unittest.TestCase):
    @patch.dict(os.environ, {"CI": "true"})
    def test_ci_env_is_non_interactive(self):
        self.assertTrue(install.is_non_interactive_mode())

    @patch.dict(os.environ, {}, clear=True)
    @patch("infra.install.sys")
    def test_no_tty_is_non_interactive(self, mock_sys):
        mock_sys.stdin.isatty.return_value = False
        mock_sys.frozen = False
        self.assertTrue(install.is_non_interactive_mode())

    @patch.dict(os.environ, {}, clear=True)
    @patch("infra.install.sys")
    def test_pyinstaller_frozen_is_non_interactive(self, mock_sys):
        mock_sys.stdin.isatty.return_value = True
        mock_sys.frozen = True
        self.assertTrue(install.is_non_interactive_mode())


class TestResolveMissingVcpp(unittest.TestCase):
    @patch("infra.install.install_vcpp_redistributable", return_value=True)
    def test_auto_install_calls_installer(self, mock_install):
        result = install.resolve_missing_vcpp(auto_install_vcpp=True)
        self.assertTrue(result)
        mock_install.assert_called_once()

    @patch("infra.install.install_vcpp_redistributable", return_value=True)
    @patch("infra.install.is_non_interactive_mode", return_value=True)
    def test_non_interactive_auto_installs(self, mock_non_interactive, mock_install):
        """Non-interactive mode should auto-install VC++ instead of skipping."""
        result = install.resolve_missing_vcpp(auto_install_vcpp=False)
        self.assertTrue(result)
        mock_install.assert_called_once()

    @patch("infra.install.install_vcpp_redistributable", return_value=True)
    @patch("infra.install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_falls_back_to_auto_install(self, mock_input, mock_non_interactive, mock_install):
        """EOFError from input() should fall back to automatic install."""
        result = install.resolve_missing_vcpp(auto_install_vcpp=False)
        self.assertTrue(result)
        mock_install.assert_called_once()


class TestResolveMissingNpmEOFError(unittest.TestCase):
    @patch("infra.install.bootstrap_npm", return_value=True)
    @patch("infra.install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_falls_back_to_bootstrap(self, mock_input, mock_non_interactive, mock_bootstrap):
        """EOFError from input() should fall back to bootstrap instead of crashing."""
        result = install.resolve_missing_npm(auto_install_npm=False, target_dir=Path("/tmp/test"))
        self.assertTrue(result)
        mock_bootstrap.assert_called_once()

    @patch("infra.install.bootstrap_npm", return_value=False)
    @patch("infra.install.is_non_interactive_mode", return_value=False)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_bootstrap_failure_degrades_gracefully(self, mock_input, mock_non_interactive, mock_bootstrap):
        """EOFError + bootstrap failure should degrade gracefully, not crash."""
        result = install.resolve_missing_npm(auto_install_npm=False, target_dir=Path("/tmp/test"))
        self.assertFalse(result)


class TestVerifyBinary(unittest.TestCase):
    @patch("infra.install.subprocess.run")
    def test_ok_binary(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        result = install.verify_binary(Path("/fake/binary"))
        self.assertEqual(result, install.BinaryStatus.OK)

    @patch("infra.install.subprocess.run")
    def test_missing_vcpp_detected(self, mock_run):
        mock_run.return_value = Mock(returncode=0xC0000135)
        result = install.verify_binary(Path("/fake/binary"))
        self.assertEqual(result, install.BinaryStatus.MISSING_VCPP)

    @patch("infra.install.subprocess.run", side_effect=OSError("not a valid Win32 app"))
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

    @patch("infra.install.io.TextIOWrapper")
    @patch("infra.install.write_manifest")
    @patch("infra.install.run_install")
    @patch("infra.install.acquire_lock")
    @patch("infra.install.get_servers_dir")
    @patch("infra.install.parse_args")
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
            mock_write_manifest.side_effect = lambda _deps: call_order.append("write_manifest")

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


class TestEnsureToolsOnDemand(unittest.TestCase):
    """An analysis installs what its repository's languages need: the base tools and those languages' servers."""

    def test_languages_get_the_base_tools_and_only_their_servers(self):
        installs, recorded, node, _ = self._ensure({"python"})
        self.assertEqual(installs, [["tokei", "python"]])
        self.assertEqual(recorded, installs)
        node.assert_called_once()

    def test_languages_without_node_servers_need_no_node_runtime(self):
        installs, _, node, _ = self._ensure({"go", "kotlin"})
        self.assertEqual(installs, [["tokei", "go", "kotlin"]])
        node.assert_not_called()

    def test_no_languages_installs_only_the_base_tools(self):
        installs, _, _, _ = self._ensure(())
        self.assertEqual(installs, [["tokei"]])

    def test_without_languages_every_tool_is_installed(self):
        installs, _, _, _ = self._ensure()
        self.assertEqual(installs, [[d.key for d in install.TOOL_REGISTRY]])

    def test_tools_already_current_are_not_installed_again(self):
        installs, _, _, needs_install = self._ensure({"typescript"}, needs=False)
        self.assertEqual(installs, [])
        needs_install.assert_called_once_with({"typescript"})

    def test_native_upgrade_through_automatic_and_cli_setup(self) -> None:
        deps = install.required_tools({"go", "rust"})
        for cli in (False, True):
            for legacy in (False, True):
                with self.subTest(cli=cli, legacy=legacy), tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp)
                    binaries = install.platform_bin_dir(target)
                    binaries.mkdir(parents=True)
                    suffix = install.exe_suffix()
                    for dep in deps:
                        (binaries / f"{dep.binary_name}{suffix}").write_bytes(b"old")

                    def download(_url: str, destination: Path, **_kwargs: object) -> bool:
                        if destination.suffix == ".gz":
                            destination.write_bytes(gzip.compress(b"new"))
                        elif destination.suffix == ".zip":
                            with zipfile.ZipFile(destination, "w") as archive:
                                archive.writestr(f"rust-analyzer{suffix}", b"new")
                        else:
                            destination.write_bytes(b"new")
                        return True

                    with (
                        patch("infra.install.get_servers_dir", return_value=target),
                        patch("infra.tool_registry.manifest.get_servers_dir", return_value=target),
                        patch("infra.install.ensure_config_template"),
                        patch("infra.install.ensure_node_runtime"),
                        patch("infra.install.resolve_npm_availability", return_value=False),
                        patch("infra.install.install_archive_tool"),
                        patch("infra.install.install_package_manager_tools"),
                        patch("infra.install.download_grammars"),
                        patch("infra.install.install_pre_commit_hooks"),
                        patch("infra.install.print_language_support_summary"),
                        patch("infra.install.verify_binary", return_value=install.BinaryStatus.OK),
                        patch("infra.tool_registry.installers.download_asset", side_effect=download),
                    ):
                        install.write_manifest(deps)
                        manifest_path = target / "installed.json"
                        manifest = json.loads(manifest_path.read_text())
                        if legacy:
                            manifest["tools"] = "one,string"
                        else:
                            manifest["tools"]["rust"] = "previous-version"
                        manifest_path.write_text(json.dumps(manifest))
                        if cli:
                            with (
                                patch.object(sys, "argv", ["codeboarding-setup"]),
                                patch.object(sys, "stdout"),
                                patch.object(sys, "stderr"),
                                patch("infra.install.io.TextIOWrapper"),
                            ):
                                install.main()
                        else:
                            install.ensure_tools(languages={"go", "rust"})
                        self.assertEqual((binaries / f"rust-analyzer{suffix}").read_bytes(), b"new")
                        self.assertEqual((binaries / f"gopls{suffix}").read_bytes(), b"new" if legacy else b"old")
                        recorded = json.loads(manifest_path.read_text())["tools"]
                        for dep in deps:
                            self.assertEqual(recorded[dep.key], install.tool_fingerprint(dep))

    def _ensure(
        self, languages=install.ALL_LANGUAGE_KEYS, needs: bool = True, **kwargs
    ) -> tuple[list[list[str]], list[list[str]], Mock, Mock]:
        installs: list[list[str]] = []
        recorded: list[list[str]] = []
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("infra.install.get_servers_dir", return_value=Path(tmp)),
            patch("infra.install.acquire_lock"),
            patch("infra.install.needs_install", return_value=needs) as needs_install,
            patch("infra.install.ensure_node_runtime") as node,
            patch("infra.install.run_install", side_effect=lambda **kw: installs.append([d.key for d in kw["deps"]])),
            patch("infra.install.write_manifest", side_effect=lambda deps: recorded.append([d.key for d in deps])),
        ):
            install.ensure_tools(languages=languages, **kwargs)
        return installs, recorded, node, needs_install


class TestEnsureLanguageServers(unittest.TestCase):
    """Per-analysis installs happen only once an entry point turns them on."""

    def tearDown(self):
        install._on_demand_install.clear()

    def test_installs_nothing_until_enabled(self):
        with patch("infra.install.ensure_tools") as ensure:
            install.ensure_language_servers({"python"})
        ensure.assert_not_called()

    def test_once_enabled_installs_the_languages_servers_with_the_entry_points_flags(self):
        install.enable_on_demand_install(auto_install_npm=True, auto_install_vcpp=False)
        with patch("infra.install.ensure_tools") as ensure:
            install.ensure_language_servers({"python", "go"})
        ensure.assert_called_once_with(auto_install_npm=True, auto_install_vcpp=False, languages={"python", "go"})


class TestRunInstallSelection(unittest.TestCase):
    """``run_install`` hands each installer only the selected tools."""

    def test_unsupported_native_platform_still_finishes_setup(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("platform.system", return_value="FreeBSD"),
            patch("infra.install.ensure_config_template"),
            patch("infra.install.install_pre_commit_hooks"),
            self.assertLogs("infra.tool_registry.installers", level="WARNING") as logs,
        ):
            install.run_install(target_dir=Path(tmp), deps=install.required_tools(()))
        self.assertTrue(any("Unsupported platform FreeBSD" in line for line in logs.output))

    def test_empty_selection_installs_no_tools(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("infra.install.ensure_config_template"),
            patch("infra.install.ensure_node_runtime") as node,
            patch("infra.install.resolve_npm_availability") as npm,
            patch("infra.install.install_node_tools") as node_tools,
            patch("infra.install.install_native_tools") as native,
            patch("infra.install.install_archive_tool") as archive,
            patch("infra.install.install_package_manager_tools") as package_manager,
            patch("infra.install.download_grammars") as grammars,
            patch("infra.install.install_pre_commit_hooks"),
            patch("infra.install.print_language_support_summary") as summary,
        ):
            install.run_install(target_dir=Path(tmp), deps=())
        for untouched in (node, npm, node_tools, native, archive, package_manager, grammars, summary):
            untouched.assert_not_called()

    def test_only_the_selected_tools_reach_the_installers(self):
        selected = [d for d in install.TOOL_REGISTRY if d.key in ("tokei", "go")]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("infra.install.ensure_config_template"),
            patch("infra.install.ensure_node_runtime") as node,
            patch("infra.install.resolve_npm_availability") as npm,
            patch("infra.install.install_node_tools") as node_tools,
            patch("infra.install.install_native_tools") as native,
            patch("infra.install.install_archive_tool") as archive,
            patch("infra.install.install_package_manager_tools") as package_manager,
            patch("infra.install.download_grammars") as grammars,
            patch("infra.install.install_pre_commit_hooks"),
            patch("infra.install.print_language_support_summary") as summary,
        ):
            install.run_install(target_dir=Path(tmp), deps=selected)
        self.assertEqual([d.key for d in native.call_args.args[1]], ["tokei", "go"])
        for untouched in (node, npm, node_tools, archive, package_manager, grammars, summary):
            untouched.assert_not_called()

    def test_kotlin_brings_its_grammar(self):
        selected = [d for d in install.TOOL_REGISTRY if d.key in ("tokei", "kotlin")]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("infra.install.ensure_config_template"),
            patch("infra.install.install_native_tools"),
            patch("infra.install.install_archive_tool") as archive,
            patch("infra.install.install_package_manager_tools"),
            patch("infra.install.download_grammars") as grammars,
            patch("infra.install.install_pre_commit_hooks"),
        ):
            install.run_install(target_dir=Path(tmp), deps=selected)
        self.assertEqual([call.args[1].key for call in archive.call_args_list], ["kotlin"])
        grammars.assert_called_once()
