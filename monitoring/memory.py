"""Process-tree resident-memory sampling for long-running analysis phases.

Static analysis spends most of its memory outside the Python heap: the LSP
server process (Roslyn/JDTLS/tsserver) and the tree-sitter C parse trees both
live in native allocations that ``sys.getsizeof`` cannot see. A run that gets
OOM-killed in CI therefore leaves no trace of *which* consumer grew.

``MemoryProbe`` samples the whole process tree (this process plus every
descendant, so LSP servers are counted) on a background thread and logs a
checkpoint line at each phase boundary. Dependency-free: reads ``/proc`` on
Linux and shells out to ``ps`` elsewhere.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Sampling cadence. Fine enough to catch a phase-length spike, coarse enough
# that the ``ps`` fallback on macOS costs nothing measurable.
DEFAULT_INTERVAL_SEC = 5.0
# How often a sample is also written to the log. Phase checkpoints alone leave
# multi-minute gaps; on a run that dies mid-phase the trend line is the evidence.
DEFAULT_LOG_INTERVAL_SEC = 60.0

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_IS_LINUX = sys.platform.startswith("linux")


def physical_memory_bytes() -> int:
    """Total RAM visible to this machine, or 0 when it cannot be determined."""
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


def _linux_process_table() -> dict[int, tuple[int, int, str]]:
    """Return ``{pid: (ppid, rss_bytes, name)}`` for every readable process."""
    table: dict[int, tuple[int, int, str]] = {}
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
        # comm may contain spaces/parens; everything after the last ')' is fixed-width.
        close_paren = stat.rfind(")")
        if close_paren == -1:
            continue
        name = stat[stat.find("(") + 1 : close_paren]
        fields = stat[close_paren + 2 :].split()
        if len(fields) < 22:
            continue
        try:
            ppid = int(fields[1])
            rss_pages = int(fields[21])
        except ValueError:
            continue
        table[pid] = (ppid, rss_pages * _PAGE_SIZE, name)
    return table


def _ps_process_table() -> dict[int, tuple[int, int, str]]:
    """``ps``-based fallback for macOS/BSD. RSS is reported in kilobytes."""
    table: dict[int, tuple[int, int, str]] = {}
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,comm="],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return table
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, rss_kb = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        table[pid] = (ppid, rss_kb * 1024, os.path.basename(parts[3]))
    return table


def process_tree_rss(root_pid: int | None = None) -> tuple[int, int, list[tuple[str, int]]]:
    """Return ``(self_rss, descendants_rss, [(name, rss), ...])`` for the tree.

    The per-descendant list is sorted largest-first so a caller can name the
    single biggest consumer without re-walking the table.
    """
    root_pid = os.getpid() if root_pid is None else root_pid
    table = _linux_process_table() if _IS_LINUX else _ps_process_table()
    if root_pid not in table:
        return 0, 0, []

    children_by_parent: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        children_by_parent.setdefault(ppid, []).append(pid)

    descendants: list[tuple[str, int]] = []
    stack = list(children_by_parent.get(root_pid, []))
    seen = {root_pid}
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        _, rss, name = table[pid]
        descendants.append((name, rss))
        stack.extend(children_by_parent.get(pid, []))

    descendants.sort(key=lambda item: item[1], reverse=True)
    return table[root_pid][1], sum(rss for _, rss in descendants), descendants


class MemoryProbe:
    """Background sampler that tracks peak process-tree RSS across phases.

    Start it once around a long analysis, call :meth:`checkpoint` at phase
    boundaries, and read :attr:`peak_total` afterwards. Every method is safe
    to call when the probe was never started — callers don't branch on it.
    """

    def __init__(
        self,
        label: str = "analysis",
        interval: float = DEFAULT_INTERVAL_SEC,
        log_interval: float = DEFAULT_LOG_INTERVAL_SEC,
    ) -> None:
        self._label = label
        self._interval = interval
        self._log_interval = log_interval
        self._next_log = 0.0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._peak_self = 0
        self._peak_children = 0
        self._peak_total = 0
        self._peak_breakdown: list[tuple[str, int]] = []

    @property
    def peak_total(self) -> int:
        with self._lock:
            return self._peak_total

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, name="memory-probe", daemon=True)
        self._thread.start()
        self.checkpoint("start")

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=self._interval + 2)
        self._thread = None
        with self._lock:
            peak_self, peak_children, peak_total = self._peak_self, self._peak_children, self._peak_total
            breakdown = list(self._peak_breakdown[:4])
        detail = ", ".join(f"{name}={format_bytes(rss)}" for name, rss in breakdown)
        logger.info(
            "mem[%s] PEAK total=%s (python=%s, children=%s)%s",
            self._label,
            format_bytes(peak_total),
            format_bytes(peak_self),
            format_bytes(peak_children),
            f" | top: {detail}" if detail else "",
        )

    def checkpoint(self, phase: str, **extra: object) -> None:
        """Log a labelled memory reading. Also folded into the running peak."""
        self_rss, children_rss, breakdown = self._record()
        detail = ", ".join(f"{name}={format_bytes(rss)}" for name, rss in breakdown[:3])
        extra_str = ", ".join(f"{key}={value}" for key, value in extra.items())
        logger.info(
            "mem[%s] %s: total=%s (python=%s, children=%s)%s%s",
            self._label,
            phase,
            format_bytes(self_rss + children_rss),
            format_bytes(self_rss),
            format_bytes(children_rss),
            f" | {extra_str}" if extra_str else "",
            f" | top: {detail}" if detail else "",
        )

    def _record(self) -> tuple[int, int, list[tuple[str, int]]]:
        self_rss, children_rss, breakdown = process_tree_rss()
        total = self_rss + children_rss
        with self._lock:
            if total > self._peak_total:
                self._peak_total = total
                self._peak_self = self_rss
                self._peak_children = children_rss
                self._peak_breakdown = breakdown
        return self_rss, children_rss, breakdown

    def _sample_loop(self) -> None:
        self._next_log = time.monotonic() + self._log_interval
        while not self._stop_event.wait(self._interval):
            try:
                self_rss, children_rss, breakdown = self._record()
                now = time.monotonic()
                if now < self._next_log:
                    continue
                self._next_log = now + self._log_interval
                detail = ", ".join(f"{name}={format_bytes(rss)}" for name, rss in breakdown[:3])
                logger.info(
                    "mem[%s] sample: total=%s (python=%s, children=%s)%s",
                    self._label,
                    format_bytes(self_rss + children_rss),
                    format_bytes(self_rss),
                    format_bytes(children_rss),
                    f" | top: {detail}" if detail else "",
                )
            except Exception:  # a sampling failure must never kill the analysis
                logger.debug("memory probe sample failed", exc_info=True)
