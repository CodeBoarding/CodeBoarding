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
                result = install.install_npm_with_nodeenv(target_dir=target_dir)
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

            result = install.install_npm_with_nodeenv(target_dir=target_dir)

        self.assertTrue(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())
        mock_requests_get.assert_not_called()
        mock_check_npm.assert_called_once_with(target_dir.resolve())

    @patch("install.preferred_node_path", return_value=None)
    def test_returns_false_when_no_node_runtime_is_available(self, mock_preferred_node_path):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=True):
            target_dir = Path(temp_dir)
            result = install.install_npm_with_nodeenv(target_dir=target_dir)

        self.assertFalse(result)
        mock_preferred_node_path.assert_called_once_with(target_dir.resolve())
