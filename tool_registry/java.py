"""Locating a Java 21+ runtime on the host."""

import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

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

    except (OSError, subprocess.TimeoutExpired) as e:
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

    Checks JAVA_HOME first, then system installations, and finally the
    system PATH as a fallback.

    Returns:
        Path to JDK home, or None if not found
    """
    java_suffix = "java.exe" if platform.system() == "Windows" else "java"

    # 1. Check JAVA_HOME environment variable
    java_home_env = os.environ.get("JAVA_HOME")
    if java_home_env:
        java_home_path = Path(java_home_env)
        java_home_java = java_home_path / "bin" / java_suffix
        if java_home_java.exists():
            version = get_java_version(str(java_home_java))
            if version >= 21:
                logger.info(f"Using JAVA_HOME Java {version} at {java_home_path}")
                return java_home_path

    # 2. Check system JDK installations
    jdks = detect_java_installations()

    for jdk in jdks:
        java_cmd = jdk / "bin" / "java"
        version = get_java_version(str(java_cmd))
        if version >= 21:
            logger.info(f"Found Java {version} at {jdk}")
            return jdk

    # 3. Check system java as fallback
    if get_java_version("java") >= 21:
        java_path = shutil.which("java")
        if java_path:
            # Resolve to JDK home (parent of parent of java executable)
            java_home = Path(java_path).resolve().parent.parent
            logger.info(f"Using system Java at {java_home}")
            return java_home

    return None
