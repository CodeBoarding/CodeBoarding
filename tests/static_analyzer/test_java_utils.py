"""
Tests for Java utility functions.
"""

import platform
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from static_analyzer.java_utils import (
    get_jdtls_config_dir,
    find_launcher_jar,
    create_jdtls_command,
)


class TestGetJdtlsConfigDir(unittest.TestCase):
    """Test JDTLS configuration directory selection."""

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_linux(self, mock_system, mock_machine):
        """Test getting config directory on Linux."""
        mock_system.return_value = "Linux"
        mock_machine.return_value = "x86_64"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_linux"))

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_macos(self, mock_system, mock_machine):
        """Test getting config directory on macOS."""
        mock_system.return_value = "Darwin"
        mock_machine.return_value = "x86_64"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_mac"))

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_windows(self, mock_system, mock_machine):
        """Test getting config directory on Windows."""
        mock_system.return_value = "Windows"
        mock_machine.return_value = "x86_64"
        jdtls_root = Path("C:/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("C:/jdtls/config_win"))

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_macos_arm64(self, mock_system, mock_machine):
        """Test getting arm64 config directory on macOS."""
        mock_system.return_value = "Darwin"
        mock_machine.return_value = "arm64"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_mac_arm"))

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_linux_arm64(self, mock_system, mock_machine):
        """Test getting arm64 config directory on Linux."""
        mock_system.return_value = "Linux"
        mock_machine.return_value = "aarch64"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_linux_arm"))

    @patch("platform.machine")
    @patch("platform.system")
    def test_get_config_dir_unsupported(self, mock_system, mock_machine):
        """Test error on unsupported platform."""
        mock_system.return_value = "FreeBSD"
        mock_machine.return_value = "x86_64"
        jdtls_root = Path("/opt/jdtls")

        with self.assertRaises(RuntimeError) as context:
            get_jdtls_config_dir(jdtls_root)

        self.assertIn("Unsupported platform", str(context.exception))


class TestFindLauncherJar(unittest.TestCase):
    """Test finding JDTLS launcher JAR."""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    def test_find_launcher_jar_success(self, mock_glob, mock_exists):
        """Test finding launcher JAR successfully."""
        mock_exists.return_value = True
        mock_glob.return_value = [Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")]

        jdtls_root = Path("/opt/jdtls")
        launcher = find_launcher_jar(jdtls_root)

        self.assertIsNotNone(launcher)
        self.assertIn("org.eclipse.equinox.launcher", str(launcher))

    @patch("pathlib.Path.exists")
    def test_find_launcher_jar_no_plugins_dir(self, mock_exists):
        """Test when plugins directory doesn't exist."""
        mock_exists.return_value = False

        jdtls_root = Path("/opt/jdtls")
        launcher = find_launcher_jar(jdtls_root)

        self.assertIsNone(launcher)

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    def test_find_launcher_jar_not_found(self, mock_glob, mock_exists):
        """Test when launcher JAR is not found."""
        mock_exists.return_value = True
        mock_glob.return_value = []

        jdtls_root = Path("/opt/jdtls")
        launcher = find_launcher_jar(jdtls_root)

        self.assertIsNone(launcher)

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    def test_find_launcher_jar_multiple_versions(self, mock_glob, mock_exists):
        """Test when multiple launcher JARs exist (returns first)."""
        mock_exists.return_value = True
        mock_glob.return_value = [
            Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar"),
            Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.500.jar"),
        ]

        jdtls_root = Path("/opt/jdtls")
        launcher = find_launcher_jar(jdtls_root)

        self.assertIsNotNone(launcher)
        # Should return the first one
        self.assertIn("1.6.400", str(launcher))


class TestCreateJdtlsCommand(unittest.TestCase):
    """Test JDTLS command creation."""

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    @patch("static_analyzer.java_utils.get_jdtls_config_dir")
    @patch("pathlib.Path.exists")
    @patch("platform.system")
    def test_create_command_success(self, mock_system, mock_exists, mock_config_dir, mock_launcher, mock_java):
        """Test creating JDTLS command successfully."""
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_launcher.return_value = Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")
        mock_config_dir.return_value = Path("/opt/jdtls/config_linux")

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")

        command = create_jdtls_command(jdtls_root, workspace_dir)

        # Verify command structure
        self.assertIsInstance(command, list)
        self.assertGreater(len(command), 10)
        self.assertIn("java", command[0])
        self.assertIn("-jar", command)
        self.assertIn("-configuration", command)
        self.assertIn("-data", command)

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    @patch("static_analyzer.java_utils.get_jdtls_config_dir")
    @patch("pathlib.Path.exists")
    @patch("platform.system")
    def test_create_command_custom_heap_size(self, mock_system, mock_exists, mock_config_dir, mock_launcher, mock_java):
        """Test creating command with custom heap size."""
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_launcher.return_value = Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")
        mock_config_dir.return_value = Path("/opt/jdtls/config_linux")

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")

        command = create_jdtls_command(jdtls_root, workspace_dir, heap_size="8G")

        # Verify heap size
        self.assertIn("-Xmx8G", command)

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    @patch("static_analyzer.java_utils.get_jdtls_config_dir")
    @patch("pathlib.Path.exists")
    @patch("platform.system")
    def test_create_command_custom_java_home(self, mock_system, mock_exists, mock_config_dir, mock_launcher, mock_java):
        """Test creating command with custom Java home."""
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_launcher.return_value = Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")
        mock_config_dir.return_value = Path("/opt/jdtls/config_linux")

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")
        custom_java = Path("/custom/java-21")

        command = create_jdtls_command(jdtls_root, workspace_dir, java_home=custom_java)

        # Should use custom Java home
        self.assertIn(str(Path("/custom/java-21/bin/java")), command[0])
        # Should not call find_java_21_or_later
        mock_java.assert_not_called()

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    def test_create_command_no_java(self, mock_java):
        """Test error when Java 21+ not found."""
        mock_java.return_value = None

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")

        with self.assertRaises(RuntimeError) as context:
            create_jdtls_command(jdtls_root, workspace_dir)

        self.assertIn("Java 21+ required", str(context.exception))

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    def test_create_command_no_launcher(self, mock_launcher, mock_java):
        """Test error when launcher JAR not found."""
        mock_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_launcher.return_value = None

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")

        with self.assertRaises(RuntimeError) as context:
            create_jdtls_command(jdtls_root, workspace_dir)

        self.assertIn("launcher JAR not found", str(context.exception))

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    @patch("static_analyzer.java_utils.get_jdtls_config_dir")
    @patch("pathlib.Path.exists")
    def test_create_command_no_config_dir(self, mock_exists, mock_config_dir, mock_launcher, mock_java):
        """Test error when config directory not found."""
        mock_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_launcher.return_value = Path("/opt/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")
        mock_config_dir.return_value = Path("/opt/jdtls/config_linux")
        mock_exists.return_value = False

        jdtls_root = Path("/opt/jdtls")
        workspace_dir = Path("/tmp/workspace")

        with self.assertRaises(RuntimeError) as context:
            create_jdtls_command(jdtls_root, workspace_dir)

        self.assertIn("config directory not found", str(context.exception))

    @patch("static_analyzer.java_utils.find_java_21_or_later")
    @patch("static_analyzer.java_utils.find_launcher_jar")
    @patch("static_analyzer.java_utils.get_jdtls_config_dir")
    @patch("pathlib.Path.exists")
    @patch("platform.system")
    def test_create_command_windows(self, mock_system, mock_exists, mock_config_dir, mock_launcher, mock_java):
        """Test creating command on Windows (java.exe)."""
        mock_system.return_value = "Windows"
        mock_exists.return_value = True
        mock_java.return_value = Path("C:/Java/jdk-21")
        mock_launcher.return_value = Path("C:/jdtls/plugins/org.eclipse.equinox.launcher_1.6.400.jar")
        mock_config_dir.return_value = Path("C:/jdtls/config_win")

        jdtls_root = Path("C:/jdtls")
        workspace_dir = Path("C:/temp/workspace")

        command = create_jdtls_command(jdtls_root, workspace_dir)

        # Should use java.exe on Windows
        self.assertIn("java.exe", command[0])


if __name__ == "__main__":
    unittest.main()
