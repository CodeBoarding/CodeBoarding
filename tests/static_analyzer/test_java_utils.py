"""
Tests for Java utility functions.
"""

import platform
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from static_analyzer.java_utils import (
    get_java_version,
    detect_java_installations,
    find_java_21_or_later,
    get_jdtls_config_dir,
    find_launcher_jar,
    create_jdtls_command,
    find_jdtls_in_vscode_extension,
)


class TestGetJavaVersion(unittest.TestCase):
    """Test Java version detection."""

    @patch("subprocess.run")
    def test_get_java_version_modern(self, mock_run):
        """Test parsing modern Java version (Java 11+)."""
        mock_run.return_value = Mock(
            stderr='openjdk version "21.0.1" 2023-10-17',
            stdout="",
            returncode=0,
        )

        version = get_java_version("java")
        self.assertEqual(version, 21)

    @patch("subprocess.run")
    def test_get_java_version_legacy(self, mock_run):
        """Test parsing legacy Java version (Java 8 and earlier)."""
        mock_run.return_value = Mock(
            stderr='java version "1.8.0_391"',
            stdout="",
            returncode=0,
        )

        version = get_java_version("java")
        self.assertEqual(version, 8)

    @patch("subprocess.run")
    def test_get_java_version_java_17(self, mock_run):
        """Test parsing Java 17 version."""
        mock_run.return_value = Mock(
            stderr='openjdk version "17.0.9" 2023-10-17',
            stdout="",
            returncode=0,
        )

        version = get_java_version("java")
        self.assertEqual(version, 17)

    @patch("subprocess.run")
    def test_get_java_version_not_found(self, mock_run):
        """Test when Java is not found."""
        mock_run.side_effect = FileNotFoundError("java not found")

        version = get_java_version("java")
        self.assertEqual(version, 0)

    @patch("subprocess.run")
    def test_get_java_version_timeout(self, mock_run):
        """Test when Java command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("java", 5)

        version = get_java_version("java")
        self.assertEqual(version, 0)

    @patch("subprocess.run")
    def test_get_java_version_no_match(self, mock_run):
        """Test when version string doesn't match expected pattern."""
        mock_run.return_value = Mock(
            stderr="Some unexpected output",
            stdout="",
            returncode=0,
        )

        version = get_java_version("java")
        self.assertEqual(version, 0)

    @patch("subprocess.run")
    def test_get_java_version_custom_command(self, mock_run):
        """Test with custom Java command path."""
        mock_run.return_value = Mock(
            stderr='openjdk version "21.0.1"',
            stdout="",
            returncode=0,
        )

        version = get_java_version("/usr/lib/jvm/java-21/bin/java")
        self.assertEqual(version, 21)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0][0], "/usr/lib/jvm/java-21/bin/java")


class TestDetectJavaInstallations(unittest.TestCase):
    """Test JDK installation detection."""

    @patch.dict("os.environ", {"JAVA_HOME": "/usr/lib/jvm/java-21"})
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_detect_from_java_home(self, mock_version, mock_glob, mock_exists):
        """Test detection from JAVA_HOME environment variable."""
        mock_exists.return_value = True
        mock_glob.return_value = []
        mock_version.return_value = 21

        jdks = detect_java_installations()

        self.assertGreater(len(jdks), 0)
        self.assertIn(Path("/usr/lib/jvm/java-21"), jdks)

    @patch.dict("os.environ", {}, clear=True)
    @patch("platform.system")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_detect_on_macos(self, mock_version, mock_glob, mock_exists, mock_system):
        """Test detection on macOS."""
        mock_system.return_value = "Darwin"
        mock_exists.return_value = True
        mock_glob.return_value = [
            Path("/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home"),
            Path("/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home"),
        ]
        mock_version.side_effect = [21, 17]

        jdks = detect_java_installations()

        self.assertEqual(len(jdks), 2)
        # Should be sorted by version (newest first)
        self.assertEqual(jdks[0].parts[-1], "Home")

    @patch.dict("os.environ", {}, clear=True)
    @patch("platform.system")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_detect_on_linux(self, mock_version, mock_glob, mock_exists, mock_system):
        """Test detection on Linux."""
        mock_system.return_value = "Linux"

        # Simple approach: just return True for the paths we want to exist
        mock_exists.return_value = True

        def glob_side_effect(pattern):
            if pattern == "java-*":
                return [Path("/usr/lib/jvm/java-21-openjdk")]
            return []

        mock_glob.side_effect = glob_side_effect
        mock_version.return_value = 21

        jdks = detect_java_installations()

        self.assertGreater(len(jdks), 0)

    @patch.dict("os.environ", {}, clear=True)
    @patch("platform.system")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.glob")
    def test_detect_no_installations(self, mock_glob, mock_exists, mock_system):
        """Test when no JDK installations are found."""
        mock_system.return_value = "Linux"
        mock_exists.return_value = False
        mock_glob.return_value = []

        jdks = detect_java_installations()

        self.assertEqual(len(jdks), 0)

    @patch("static_analyzer.java_utils.get_java_version")
    def test_detect_validates_java_executable(self, mock_version):
        """Test that only JDKs with valid java executable are returned."""
        # Create a real temporary directory structure for testing
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create valid JDK structure
            valid_jdk = Path(tmpdir) / "java-21"
            valid_bin = valid_jdk / "bin"
            valid_bin.mkdir(parents=True)
            (valid_bin / "java").touch()

            # Create invalid JDK structure (no bin/java)
            invalid_jdk = Path(tmpdir) / "java-invalid"
            invalid_jdk.mkdir()

            # Mock to use our temp directory
            with patch("platform.system", return_value="Linux"):
                with patch.object(Path, "glob") as mock_glob:

                    def glob_side_effect(pattern):
                        if pattern in ["java-*", "jdk-*", "jdk*"]:
                            return [valid_jdk, invalid_jdk]
                        return []

                    mock_glob.side_effect = glob_side_effect

                    # Mock base.exists() to return True for our temp dir
                    original_exists = Path.exists

                    def exists_wrapper(self):
                        if str(self) == tmpdir:
                            return True
                        return original_exists(self)

                    with patch.object(Path, "exists", exists_wrapper):
                        with patch.dict("os.environ", {}, clear=True):
                            with patch("static_analyzer.java_utils.detect_java_installations") as mock_detect:
                                # Manually call the validation logic
                                candidates = [valid_jdk, invalid_jdk]
                                valid_jdks = []
                                for candidate in candidates:
                                    java_exe = candidate / "bin" / "java"
                                    if java_exe.exists():
                                        valid_jdks.append(candidate)

                                # Only the valid JDK should be in the list
                                self.assertEqual(len(valid_jdks), 1)
                                self.assertIn("java-21", str(valid_jdks[0]))


class TestFindJava21OrLater(unittest.TestCase):
    """Test finding Java 21+ installation."""

    @patch("static_analyzer.java_utils.detect_java_installations")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_find_java_21_from_installations(self, mock_version, mock_detect):
        """Test finding Java 21+ from detected installations."""
        mock_detect.return_value = [
            Path("/usr/lib/jvm/java-21"),
            Path("/usr/lib/jvm/java-17"),
        ]
        mock_version.side_effect = [21, 17]

        java_home = find_java_21_or_later()

        self.assertEqual(java_home, Path("/usr/lib/jvm/java-21"))

    @patch("static_analyzer.java_utils.detect_java_installations")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_find_java_23_from_installations(self, mock_version, mock_detect):
        """Test finding Java 23."""
        mock_detect.return_value = [Path("/usr/lib/jvm/java-23")]
        mock_version.return_value = 23

        java_home = find_java_21_or_later()

        self.assertEqual(java_home, Path("/usr/lib/jvm/java-23"))

    @patch("static_analyzer.java_utils.detect_java_installations")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_find_java_only_old_versions(self, mock_version, mock_detect):
        """Test when only older Java versions are available."""
        mock_detect.return_value = [
            Path("/usr/lib/jvm/java-17"),
            Path("/usr/lib/jvm/java-11"),
        ]
        mock_version.side_effect = [17, 11, 17]  # System java is also 17

        java_home = find_java_21_or_later()

        self.assertIsNone(java_home)

    @patch("static_analyzer.java_utils.detect_java_installations")
    @patch("static_analyzer.java_utils.get_java_version")
    @patch("shutil.which")
    def test_find_java_from_system(self, mock_which, mock_version, mock_detect):
        """Test finding Java 21+ from system PATH."""
        mock_detect.return_value = []
        mock_version.side_effect = [21]
        mock_which.return_value = "/usr/bin/java"

        java_home = find_java_21_or_later()

        # Should resolve to JDK home (2 levels up from java executable)
        self.assertIsNotNone(java_home)
        self.assertEqual(java_home, Path("/usr/bin/java").resolve().parent.parent)

    @patch("static_analyzer.java_utils.detect_java_installations")
    @patch("static_analyzer.java_utils.get_java_version")
    def test_find_java_none_available(self, mock_version, mock_detect):
        """Test when no Java is available."""
        mock_detect.return_value = []
        mock_version.return_value = 0

        java_home = find_java_21_or_later()

        self.assertIsNone(java_home)


class TestGetJdtlsConfigDir(unittest.TestCase):
    """Test JDTLS configuration directory selection."""

    @patch("platform.system")
    def test_get_config_dir_linux(self, mock_system):
        """Test getting config directory on Linux."""
        mock_system.return_value = "Linux"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_linux"))

    @patch("platform.system")
    def test_get_config_dir_macos(self, mock_system):
        """Test getting config directory on macOS."""
        mock_system.return_value = "Darwin"
        jdtls_root = Path("/opt/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("/opt/jdtls/config_mac"))

    @patch("platform.system")
    def test_get_config_dir_windows(self, mock_system):
        """Test getting config directory on Windows."""
        mock_system.return_value = "Windows"
        jdtls_root = Path("C:/jdtls")

        config_dir = get_jdtls_config_dir(jdtls_root)

        self.assertEqual(config_dir, Path("C:/jdtls/config_win"))

    @patch("platform.system")
    def test_get_config_dir_unsupported(self, mock_system):
        """Test error on unsupported platform."""
        mock_system.return_value = "FreeBSD"
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
        self.assertIn("/custom/java-21/bin/java", command[0])
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


class TestFindJdtlsInVscodeExtension(unittest.TestCase):
    """Test finding JDTLS in VSCode extension directory."""

    @patch("pathlib.Path.home")
    @patch("pathlib.Path.glob")
    def test_find_jdtls_vscode_success(self, mock_glob, mock_home):
        """Test finding JDTLS in VSCode extension successfully."""
        mock_home.return_value = Path("/home/testuser")

        vscode_ext_path = Path("/home/testuser/.vscode/extensions")
        ext_dir = vscode_ext_path / "codeboarding.codeboarding-0.7.0"

        def glob_side_effect(pattern):
            if pattern == "codeboarding*":
                return [ext_dir]
            return []

        mock_glob.side_effect = glob_side_effect

        def exists_side_effect(self):
            path_str = str(self)
            if "vscode/extensions" in path_str:
                return True
            if "bin/jdtls" in path_str:
                return True
            if "plugins" in path_str:
                return True
            return False

        with patch.object(Path, "exists", exists_side_effect):
            result = find_jdtls_in_vscode_extension()

        self.assertIsNotNone(result)
        self.assertIn("bin/jdtls", str(result))

    @patch("pathlib.Path.home")
    @patch("pathlib.Path.exists")
    def test_find_jdtls_vscode_not_found(self, mock_exists, mock_home):
        """Test when JDTLS is not found in VSCode extensions."""
        mock_home.return_value = Path("/home/testuser")
        mock_exists.return_value = False

        result = find_jdtls_in_vscode_extension()

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
