from __future__ import annotations

import json
from pathlib import Path

import pytest

from static_analyzer.engine.adapters.cpp_cdb.cdb_io import (
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
