from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone


def generate_header(title: str, timestamp: str | None = None, extra_lines: list[str] | None = None) -> str:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    lines = [f"# {title}", "", f"**Generated:** {ts}", ""]
    if extra_lines:
        lines.extend(extra_lines)
        lines.append("")
    return "\n".join(lines)


def generate_system_specs() -> str:
    lines = [
        "## System Specifications",
        "",
    ]

    # Get OS information
    os_name = platform.system()
    os_version = platform.platform()
    lines.append(f"**Operating System:** {os_name} ({os_version})")

    # Get CPU information
    processor = platform.processor()
    if not processor or processor == "":
        processor = platform.machine()
    lines.append(f"**Processor:** {processor}")

    # Get number of cores
    cpu_count = os.cpu_count()
    lines.append(f"**CPU Cores:** {cpu_count}")

    # Get git user information (with graceful fallback)
    try:
        git_name_result = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)

        if git_name_result.returncode == 0:
            git_name = git_name_result.stdout.strip()
            lines.append(f"**Git User:** {git_name}")
        else:
            lines.append("**Git User:** Not configured")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        lines.append("**Git User:** Not available")

    lines.append("")
    return "\n".join(lines)
