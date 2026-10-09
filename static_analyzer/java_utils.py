import logging
import platform
from pathlib import Path

from tool_registry.java import find_java_21_or_later

logger = logging.getLogger(__name__)


def _is_arm64(machine: str) -> bool:
    """Return True for arm64/aarch64 (case-insensitive)."""
    return machine.lower() in ("arm64", "aarch64")


def get_jdtls_config_dir(jdtls_root: Path) -> Path:
    """Get platform- and arch-specific JDTLS configuration directory."""
    system = platform.system()
    machine = platform.machine()

    if system == "Linux":
        return jdtls_root / ("config_linux_arm" if _is_arm64(machine) else "config_linux")
    elif system == "Darwin":
        return jdtls_root / ("config_mac_arm" if _is_arm64(machine) else "config_mac")
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
