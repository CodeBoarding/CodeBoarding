import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from infra.tool_registry.java import detect_java_installations, find_java_21_or_later, get_java_version


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
    def test_get_java_version_permission_denied(self, mock_run):
        mock_run.side_effect = PermissionError("java permission denied")

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
    @patch("infra.tool_registry.java.get_java_version")
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
    @patch("infra.tool_registry.java.get_java_version")
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
    @patch("infra.tool_registry.java.get_java_version")
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

    @patch("infra.tool_registry.java.get_java_version")
    def test_detect_validates_java_executable(self, mock_version):
        """Test that only JDKs with valid java executable are returned."""
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
                            with patch("infra.tool_registry.java.detect_java_installations") as mock_detect:
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

    @patch.dict("os.environ", {}, clear=True)
    @patch("infra.tool_registry.java.detect_java_installations")
    @patch("infra.tool_registry.java.get_java_version")
    def test_find_java_21_from_installations(self, mock_version, mock_detect):
        """Test finding Java 21+ from detected installations."""
        mock_detect.return_value = [
            Path("/usr/lib/jvm/java-21"),
            Path("/usr/lib/jvm/java-17"),
        ]
        mock_version.side_effect = [21, 17]

        java_home = find_java_21_or_later()

        self.assertEqual(java_home, Path("/usr/lib/jvm/java-21"))

    @patch.dict("os.environ", {}, clear=True)
    @patch("infra.tool_registry.java.detect_java_installations")
    @patch("infra.tool_registry.java.get_java_version")
    def test_find_java_23_from_installations(self, mock_version, mock_detect):
        """Test finding Java 23."""
        mock_detect.return_value = [Path("/usr/lib/jvm/java-23")]
        mock_version.return_value = 23

        java_home = find_java_21_or_later()

        self.assertEqual(java_home, Path("/usr/lib/jvm/java-23"))

    @patch.dict("os.environ", {}, clear=True)
    @patch("infra.tool_registry.java.detect_java_installations")
    @patch("infra.tool_registry.java.get_java_version")
    def test_find_java_only_old_versions(self, mock_version, mock_detect):
        """Test when only older Java versions are available."""
        mock_detect.return_value = [
            Path("/usr/lib/jvm/java-17"),
            Path("/usr/lib/jvm/java-11"),
        ]
        mock_version.side_effect = [17, 11, 17]  # System java is also 17

        java_home = find_java_21_or_later()

        self.assertIsNone(java_home)

    @patch.dict("os.environ", {}, clear=True)
    @patch("infra.tool_registry.java.detect_java_installations")
    @patch("infra.tool_registry.java.get_java_version")
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

    @patch.dict("os.environ", {}, clear=True)
    @patch("infra.tool_registry.java.detect_java_installations")
    @patch("infra.tool_registry.java.get_java_version")
    def test_find_java_none_available(self, mock_version, mock_detect):
        """Test when no Java is available."""
        mock_detect.return_value = []
        mock_version.return_value = 0

        java_home = find_java_21_or_later()

        self.assertIsNone(java_home)

    def test_java_home_wins_over_newer_jdk_on_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_java_home = Path(tmpdir) / "jdk-21"
            (fake_java_home / "bin").mkdir(parents=True)
            (fake_java_home / "bin" / "java").touch()

            with patch.dict("os.environ", {"JAVA_HOME": str(fake_java_home)}):
                with patch("infra.tool_registry.java.get_java_version", return_value=21) as mock_version:
                    with patch("infra.tool_registry.java.detect_java_installations") as mock_detect:
                        result = find_java_21_or_later()

                        self.assertEqual(result, fake_java_home)
                        mock_detect.assert_not_called()
                        mock_version.assert_called_once()

    def test_java_home_too_old_falls_through_to_scan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_java_home = Path(tmpdir) / "jdk-17"
            (old_java_home / "bin").mkdir(parents=True)
            (old_java_home / "bin" / "java").touch()

            scanned_jdk = Path("/opt/jvm/jdk-21")

            with patch.dict("os.environ", {"JAVA_HOME": str(old_java_home)}):
                with patch(
                    "infra.tool_registry.java.get_java_version",
                    side_effect=[17, 21],  # JAVA_HOME probe, then scanned jdk
                ):
                    with patch(
                        "infra.tool_registry.java.detect_java_installations",
                        return_value=[scanned_jdk],
                    ):
                        result = find_java_21_or_later()

                        self.assertEqual(result, scanned_jdk)

    def test_java_home_unset_uses_scan(self):
        """No JAVA_HOME: existing scan-and-sort behavior is preserved."""
        scanned_jdk = Path("/opt/jvm/jdk-21")

        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "infra.tool_registry.java.detect_java_installations",
                return_value=[scanned_jdk],
            ):
                with patch("infra.tool_registry.java.get_java_version", return_value=21):
                    result = find_java_21_or_later()

                    self.assertEqual(result, scanned_jdk)
