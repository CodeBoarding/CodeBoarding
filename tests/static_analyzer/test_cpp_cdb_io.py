from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from static_analyzer.engine.adapters.cpp_cdb.cdb_io import (
    cdb_generation_lock,
    is_valid_compile_commands,
    read_compile_commands,
    validate_compile_commands,
    write_compile_commands_atomic,
)


VALID_ENTRY = {"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}


def test_write_compile_commands_atomic_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "compile_commands.json"
    write_compile_commands_atomic(target, [VALID_ENTRY])
    assert read_compile_commands(target) == [VALID_ENTRY]
    assert not list(tmp_path.glob(".compile_commands.json.*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"directory": ".", "command": "c++ -c x.cc"}],
        [{"directory": ".", "file": "x.cc"}],
        [{"directory": ".", "file": "x.cc", "arguments": []}],
    ],
)
def test_validate_compile_commands_rejects_invalid_entries(payload: object) -> None:
    with pytest.raises(ValueError):
        validate_compile_commands(payload)


def test_is_valid_compile_commands_rejects_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / "compile_commands.json"
    target.write_text("not json")
    assert is_valid_compile_commands(target) is False


def test_is_valid_compile_commands_accepts_arguments(tmp_path: Path) -> None:
    target = tmp_path / "compile_commands.json"
    target.write_text(json.dumps([{"directory": ".", "file": "x.cc", "arguments": ["c++", "-c", "x.cc"]}]))
    assert is_valid_compile_commands(target) is True


def test_cdb_generation_lock_serializes_concurrent_holders(tmp_path: Path) -> None:
    """Two threads acquiring the lock must run strictly one-at-a-time.

    Why: concurrent CDB generators for the same repo would race on
    ``compile_commands.json`` writes. The second thread must only observe
    the entered state after the first has released.
    """
    cdb_dir = tmp_path / "cdb"
    holding = threading.Event()
    release_first = threading.Event()
    ordering: list[str] = []

    def first() -> None:
        with cdb_generation_lock(cdb_dir):
            ordering.append("first-enter")
            holding.set()
            release_first.wait(timeout=5)
            ordering.append("first-exit")

    def second() -> None:
        holding.wait(timeout=5)
        with cdb_generation_lock(cdb_dir):
            ordering.append("second-enter")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    # Give t2 time to block on the lock before we allow t1 to release.
    time.sleep(0.1)
    assert ordering == ["first-enter"], f"second thread must not enter before first releases: {ordering}"
    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert ordering == ["first-enter", "first-exit", "second-enter"]
    assert (cdb_dir / ".lock").is_file(), "lock file must remain after release (mirrors .download.lock)"
