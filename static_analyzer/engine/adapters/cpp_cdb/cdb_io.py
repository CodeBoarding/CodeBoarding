"""Generated compilation-database IO helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


_CDB_FILENAME = "compile_commands.json"


def validate_compile_commands(payload: object, *, require_non_empty: bool = True) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("compile_commands.json must contain a JSON array")
    if require_non_empty and not payload:
        raise ValueError("compile_commands.json has no entries")

    entries: list[dict] = []
    for idx, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"compile_commands.json entry {idx} must be an object")
        if not _non_empty_string(entry.get("directory")):
            raise ValueError(f"compile_commands.json entry {idx} is missing 'directory'")
        if not _non_empty_string(entry.get("file")):
            raise ValueError(f"compile_commands.json entry {idx} is missing 'file'")
        if not (_valid_arguments(entry.get("arguments")) or _non_empty_string(entry.get("command"))):
            raise ValueError(f"compile_commands.json entry {idx} is missing 'arguments' or 'command'")
        entries.append(entry)
    return entries


def read_compile_commands(path: Path, *, require_non_empty: bool = True) -> list[dict]:
    return validate_compile_commands(json.loads(path.read_text(encoding="utf-8")), require_non_empty=require_non_empty)


def is_valid_compile_commands(path: Path, *, require_non_empty: bool = True) -> bool:
    try:
        read_compile_commands(path, require_non_empty=require_non_empty)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return True


def write_compile_commands_atomic(path: Path, entries: list[dict]) -> None:
    validate_compile_commands(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, indent=2)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def clear_generated_compile_commands(cdb_dir: Path) -> None:
    (cdb_dir / _CDB_FILENAME).unlink(missing_ok=True)
    if not cdb_dir.is_dir():
        return
    for tmp in cdb_dir.glob(f".{_CDB_FILENAME}.*.tmp"):
        tmp.unlink(missing_ok=True)
    for tmp in cdb_dir.glob(f".{_CDB_FILENAME}.*.build"):
        tmp.unlink(missing_ok=True)


def temp_compile_commands_path(cdb_dir: Path) -> Path:
    cdb_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{_CDB_FILENAME}.", suffix=".build", dir=str(cdb_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.unlink(missing_ok=True)
    return tmp_path


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_arguments(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(arg, str) for arg in value)
