"""Shared utilities for the engine package."""

from __future__ import annotations

import ctypes
import functools
import os
import platform
from pathlib import Path
from urllib.parse import unquote, urlparse

_IS_WINDOWS = platform.system() == "Windows"


@functools.lru_cache(maxsize=4096)
def uri_to_path(uri: str) -> Path | None:
    """Convert a file URI to a Path; None for empty, non-file, or parse errors."""
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        path = unquote(parsed.path)
        if _IS_WINDOWS:
            # Strip the leading slash on ``/C:/foo`` -> ``C:/foo``.
            if len(path) >= 3 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return Path(path).resolve()
        return Path(path)
    except Exception:
        return None


def definition_location(definition: dict) -> tuple[Path, int, int] | None:
    """Return the file and start position from an LSP definition result."""
    uri = definition.get("targetUri", definition.get("uri", ""))
    file_path = uri_to_path(uri)
    if file_path is None:
        return None

    selection_range = definition.get(
        "targetSelectionRange",
        definition.get("targetRange", definition.get("range", {})),
    )
    start = selection_range.get("start", {})
    line = start.get("line")
    character = start.get("character")
    if not isinstance(line, int) or not isinstance(character, int):
        return None
    return file_path, line, character


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_ram_gb() -> float | None:
    """Return total physical RAM in gibibytes, or None if the platform
    doesn't expose one of the supported probes."""
    if hasattr(os, "sysconf"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            return (page * pages) / (1024**3)
        except (ValueError, OSError):
            return None
    if _IS_WINDOWS:
        try:
            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return None
            return stat.ullTotalPhys / (1024**3)
        except (OSError, AttributeError):
            return None
    return None
