import os
import subprocess
import shutil
import re
import platform
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def get_java_version(java_cmd: str = "java") -> int:
    try:
        result = subprocess.run([java_cmd, "-version"], capture_output=True, text=True, timeout=5)

        # Output is typically on stderr
        output = result.stderr or result.stdout

        # Parse version from lines like:
        # java version "21.0.1"
        # openjdk version "21.0.1"
        match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
        if match:
            major = int(match.group(1))
            # Java 8 and earlier used "1.8", "1.7" format
            if major == 1 and match.group(2):
                return int(match.group(2))
            return major

        return 0

    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Failed to get Java version: {e}")
        return 0


def detect_java_installations() -> list[Path]:
    """
    Detect JDK installations on the system.

    Returns:
        List of paths to JDK home directories
    """
    candidates = []

    # Check JAVA_HOME
    if java_home := os.getenv("JAVA_HOME"):
        candidates.append(Path(java_home))

    # Platform-specific search paths
    system = platform.system()

    if system == "Darwin":  # macOS
        # Standard macOS JDK location
        jvm_dir = Path("/Library/Java/JavaVirtualMachines")
        if jvm_dir.exists():
            candidates.extend(jvm_dir.glob("*/Contents/Home"))

    elif system == "Linux":
        # Common Linux JDK locations
        for base_dir in ["/usr/lib/jvm", "/usr/java", "/opt/java"]:
            base = Path(base_dir)
            if base.exists():
                candidates.extend(base.glob("java-*"))
                candidates.extend(base.glob("jdk-*"))
                candidates.extend(base.glob("jdk*"))

    elif system == "Windows":
        # Common Windows JDK locations
        for base_dir in [
            "C:/Program Files/Java",
            "C:/Program Files/Eclipse Adoptium",
            "C:/Program Files/Amazon Corretto",
            "C:/Program Files (x86)/Java",
        ]:
            base = Path(base_dir)
            if base.exists():
                candidates.extend(base.glob("jdk-*"))
                candidates.extend(base.glob("jdk*"))

    # Validate candidates
    valid_jdks = []
    for candidate in candidates:
        java_exe = candidate / "bin" / ("java.exe" if system == "Windows" else "java")
        if java_exe.exists():
            valid_jdks.append(candidate)

    # Remove duplicates and sort by version (newest first)
    unique_jdks = list(dict.fromkeys(valid_jdks))
    unique_jdks.sort(key=lambda jdk: get_java_version(str(jdk / "bin" / "java")), reverse=True)

    return unique_jdks


def find_java_21_or_later() -> Path | None:
    """
    Find a Java 21+ installation.

    Returns:
        Path to JDK home, or None if not found
    """
    jdks = detect_java_installations()

    for jdk in jdks:
        java_cmd = jdk / "bin" / "java"
        version = get_java_version(str(java_cmd))
        if version >= 21:
            logger.info(f"Found Java {version} at {jdk}")
            return jdk

    # Check system java as fallback
    if get_java_version("java") >= 21:
        java_path = shutil.which("java")
        if java_path:
            # Resolve to JDK home (parent of parent of java executable)
            java_home = Path(java_path).resolve().parent.parent
            logger.info(f"Using system Java at {java_home}")
            return java_home

    return None


def get_jdtls_config_dir(jdtls_root: Path) -> Path:
    """
    Get platform-specific JDTLS configuration directory.

    Args:
        jdtls_root: Root directory of JDTLS installation

    Returns:
        Path to config_linux, config_mac, or config_win
    """
    system = platform.system()

    if system == "Linux":
        return jdtls_root / "config_linux"
    elif system == "Darwin":
        return jdtls_root / "config_mac"
    elif system == "Windows":
        return jdtls_root / "config_win"
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def find_launcher_jar(jdtls_root: Path) -> Path | None:
    """
    Find the Eclipse Equinox launcher JAR.

    Args:
        jdtls_root: Root directory of JDTLS installation

    Returns:
        Path to launcher JAR or None if not found
    """
    plugins_dir = jdtls_root / "plugins"
    if not plugins_dir.exists():
        return None

    # Find JAR matching org.eclipse.equinox.launcher_*.jar
    launchers = list(plugins_dir.glob("org.eclipse.equinox.launcher_*.jar"))

    if not launchers:
        return None

    # Return the first (should only be one)
    return launchers[0]


def create_jdtls_command(
    jdtls_root: Path, workspace_dir: Path, java_home: Path | None = None, heap_size: str = "4G"
) -> list[str]:
    """
    Create command to launch JDTLS.

    Args:
        jdtls_root: Root directory of JDTLS installation
        workspace_dir: Workspace data directory for this project
        java_home: Path to JDK to use (default: auto-detect Java 21+)
        heap_size: JVM heap size (default: 4G)

    Returns:
        Command as list of strings

    Raises:
        RuntimeError: If Java 21+ not found or JDTLS components missing
    """
    # Find Java 21+
    if java_home is None:
        java_home = find_java_21_or_later()
        if java_home is None:
            raise RuntimeError("Java 21+ required to run JDTLS. Please install JDK 21 or later.")

    java_cmd = java_home / "bin" / ("java.exe" if platform.system() == "Windows" else "java")

    # Find launcher JAR
    launcher_jar = find_launcher_jar(jdtls_root)
    if launcher_jar is None:
        raise RuntimeError(f"JDTLS launcher JAR not found in {jdtls_root}/plugins")

    # Get config directory
    config_dir = get_jdtls_config_dir(jdtls_root)
    if not config_dir.exists():
        raise RuntimeError(f"JDTLS config directory not found: {config_dir}")

    # Build command
    command = [
        str(java_cmd),
        "-Declipse.application=org.eclipse.jdt.ls.core.id1",
        "-Dosgi.bundles.defaultStartLevel=4",
        "-Declipse.product=org.eclipse.jdt.ls.core.product",
        "-Dlog.level=WARNING",
        f"-Xmx{heap_size}",
        f"-Xms{heap_size}",  # Match Xms to Xmx to avoid heap resizing pauses
        "-jar",
        str(launcher_jar),
        "-configuration",
        str(config_dir),
        "-data",
        str(workspace_dir),
        "--add-modules=ALL-SYSTEM",
        "--add-opens",
        "java.base/java.util=ALL-UNNAMED",
        "--add-opens",
        "java.base/java.lang=ALL-UNNAMED",
    ]

    return command
