import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
    @patch("install.install_npm_with_nodeenv", return_value=True)
    def test_auto_install_mode_uses_nodeenv(self, mock_install_npm):
        target_dir = Path("/tmp/codeboarding-servers")
        result = install.resolve_missing_npm(auto_install_npm=True, target_dir=target_dir)
        self.assertTrue(result)
        mock_install_npm.assert_called_once_with(target_dir=target_dir)

    @patch("install.is_non_interactive_mode", return_value=True)
    @patch("install.install_npm_with_nodeenv")
    def test_non_interactive_without_auto_install_raises(self, mock_install_npm, mock_non_interactive):
        with self.assertRaises(SystemExit):
            install.resolve_missing_npm(auto_install_npm=False)
        mock_non_interactive.assert_called_once()
        mock_install_npm.assert_not_called()


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


class TestInstallNpmWithNodeenv(unittest.TestCase):
    @patch("install.check_npm", return_value=True)
    @patch("install._ensure_local_nodeenv_on_path")
    @patch("install._run_nodeenv")
    @patch("install._has_active_python_virtualenv", return_value=True)
    def test_uses_python_virtualenv_when_available(
        self,
        mock_virtualenv,
        mock_run_nodeenv,
        mock_ensure_local_nodeenv_on_path,
        mock_check_npm,
    ):
        target_dir = Path("/tmp/codeboarding-servers")

        result = install.install_npm_with_nodeenv(target_dir=target_dir)

        self.assertTrue(result)
        mock_virtualenv.assert_called_once()
        mock_run_nodeenv.assert_called_once_with(["--python-virtualenv"])
        mock_ensure_local_nodeenv_on_path.assert_called_once_with(target_dir.resolve())
        mock_check_npm.assert_called_once_with(target_dir.resolve())

    @patch("install.check_npm", return_value=True)
    @patch("install._ensure_local_nodeenv_on_path")
    @patch("install._run_nodeenv")
    @patch("install._has_active_python_virtualenv", return_value=False)
    def test_uses_standalone_nodeenv_when_no_virtualenv(
        self,
        mock_virtualenv,
        mock_run_nodeenv,
        mock_ensure_local_nodeenv_on_path,
        mock_check_npm,
    ):
        target_dir = Path("/tmp/codeboarding-servers")

        result = install.install_npm_with_nodeenv(target_dir=target_dir)

        self.assertTrue(result)
        mock_virtualenv.assert_called_once()
        mock_run_nodeenv.assert_called_once_with([str(target_dir.resolve() / "nodeenv")])
        mock_ensure_local_nodeenv_on_path.assert_called_once_with(target_dir.resolve())
        mock_check_npm.assert_called_once_with(target_dir.resolve())
