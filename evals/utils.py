from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone


def get_git_user() -> str:
    """Get the git user name configured locally."""
    try:
        git_name_result = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        if git_name_result.returncode == 0:
            return git_name_result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        pass
    return "unknown"


def get_git_commit_short() -> str:
    """Get the short (7-char) git commit hash of the current HEAD."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            commit_hash = result.stdout.strip()
            return commit_hash[:7] if len(commit_hash) >= 7 else commit_hash
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        pass
    return "unknown"


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

    # Get git commit information (hash and message)
    try:
        git_commit_result = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        git_message_result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if git_commit_result.returncode == 0:
            commit_hash = git_commit_result.stdout.strip()
            short_hash = commit_hash[:7] if len(commit_hash) >= 7 else commit_hash
            lines.append(f"**Commit:** {short_hash}")

        if git_message_result.returncode == 0:
            commit_message = git_message_result.stdout.strip()
            # Truncate long commit messages for readability
            if len(commit_message) > 80:
                commit_message = commit_message[:77] + "..."
            lines.append(f"**Commit Message:** {commit_message}")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        lines.append("**Commit:** Not available")

    lines.append("")
    return "\n".join(lines)
