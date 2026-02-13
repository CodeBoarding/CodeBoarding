import sys
import unittest
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
        result = install.resolve_missing_npm(auto_install_npm=True)
        self.assertTrue(result)
        mock_install_npm.assert_called_once()

    @patch("install.is_non_interactive_mode", return_value=True)
    @patch("install.install_npm_with_nodeenv")
    def test_non_interactive_without_auto_install_returns_false(self, mock_install_npm, mock_non_interactive):
        result = install.resolve_missing_npm(auto_install_npm=False)
        self.assertFalse(result)
        mock_non_interactive.assert_called_once()
        mock_install_npm.assert_not_called()


class TestResolveNpmAvailability(unittest.TestCase):
    @patch("install.resolve_missing_npm")
    @patch("install.check_npm", return_value=True)
    def test_npm_present_does_not_attempt_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        result = install.resolve_npm_availability(auto_install_npm=False)
        self.assertTrue(result)
        mock_check_npm.assert_called_once()
        mock_resolve_missing_npm.assert_not_called()

    @patch("install.resolve_missing_npm", return_value=True)
    @patch("install.check_npm", return_value=False)
    def test_missing_npm_attempts_remediation(self, mock_check_npm, mock_resolve_missing_npm):
        result = install.resolve_npm_availability(auto_install_npm=True)
        self.assertTrue(result)
        mock_check_npm.assert_called_once()
        mock_resolve_missing_npm.assert_called_once_with(auto_install_npm=True)
