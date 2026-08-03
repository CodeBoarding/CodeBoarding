"""Process-tree memory helpers used to enforce the LSP budget."""

import os
import subprocess
import sys

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_IS_LINUX = sys.platform.startswith("linux")


def physical_memory_bytes() -> int:
    """Return total physical memory, or zero when it cannot be determined."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0


def format_bytes(num_bytes: float) -> str:
    """Render a byte count as a short human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f}TB"


def _linux_process_table() -> dict[int, tuple[int, int]]:
    """Return each readable Linux process as ``{pid: (ppid, rss_bytes)}``."""
    table: dict[int, tuple[int, int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return table

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                stat = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue

        close_paren = stat.rfind(")")
        if close_paren == -1:
            continue
        fields = stat[close_paren + 2 :].split()
        if len(fields) < 22:
            continue
        try:
            table[pid] = (int(fields[1]), int(fields[21]) * _PAGE_SIZE)
        except ValueError:
            continue
    return table


def _ps_process_table() -> dict[int, tuple[int, int]]:
    """Return each readable macOS/BSD process as ``{pid: (ppid, rss_bytes)}``."""
    table: dict[int, tuple[int, int]] = {}
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss="],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return table

    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            pid, ppid, rss_kb = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        table[pid] = (ppid, rss_kb * 1024)
    return table


def process_tree_rss(root_pid: int) -> int:
    """Return resident memory held by a process and all its descendants."""
    table = _linux_process_table() if _IS_LINUX else _ps_process_table()
    if root_pid not in table:
        return 0

    children_by_parent: dict[int, list[int]] = {}
    for pid, (parent_pid, _) in table.items():
        children_by_parent.setdefault(parent_pid, []).append(pid)

    total = 0
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += table[pid][1]
        stack.extend(children_by_parent.get(pid, []))
    return total
