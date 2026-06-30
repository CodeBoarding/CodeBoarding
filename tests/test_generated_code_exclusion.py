"""Generated code (ctypes/clang2py bindings, protobuf) is excluded from static analysis.

Why: such files carry huge symbol counts but no architectural signal and explode the LSP
references phase — e.g. tinygrad's runtime/autogen bindings timed out `codeboarding full`.
"""

from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.engine.language_adapter import _is_generated_source

_PB2_FILES = ("schema_pb2.py", "schema_pb2.pyi", "schema_pb2_grpc.py", "schema_pb2_grpc.pyi")


def test_ignore_patterns_exclude_generated_bindings(tmp_path: Path) -> None:
    (tmp_path / "pkg" / "runtime" / "autogen").mkdir(parents=True)
    (tmp_path / "pkg" / "runtime" / "autogen" / "nv.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "_autogen").mkdir(parents=True)
    (tmp_path / "pkg" / "_autogen" / "gen.py").write_text("x = 1\n")
    (tmp_path / "proto").mkdir()
    for name in _PB2_FILES:
        (tmp_path / "proto" / name).write_text("x = 1\n")
    (tmp_path / "pkg" / "tensor.py").write_text("x = 1\n")

    im = RepoIgnoreManager(tmp_path)

    assert im.should_ignore(tmp_path / "pkg" / "runtime" / "autogen" / "nv.py")
    assert im.should_ignore(tmp_path / "pkg" / "_autogen" / "gen.py")
    for name in _PB2_FILES:
        assert im.should_ignore(tmp_path / "proto" / name)
    assert not im.should_ignore(tmp_path / "pkg" / "tensor.py")


def test_is_generated_source_flags_large_ctypes_binding(tmp_path: Path) -> None:
    big = tmp_path / "bindings.py"
    big.write_text("import ctypes\n" + "CONST = ctypes.c_int(1)\n" * 30000)  # ~720 KB > 512 KB
    assert big.stat().st_size > 512 * 1024
    assert _is_generated_source(big)


def test_is_generated_source_keeps_handwritten_ctypes(tmp_path: Path) -> None:
    """A small hand-written file importing ctypes must NOT be flagged (size guard)."""
    hand = tmp_path / "device.py"
    hand.write_text("import ctypes\n\n\ndef alloc(n):\n    return ctypes.create_string_buffer(n)\n")
    assert not _is_generated_source(hand)


def test_is_generated_source_keeps_file_mentioning_generated(tmp_path: Path) -> None:
    """Regression: a hand-written file that merely mentions 'auto-generated' in a comment (e.g.
    Java records' accessors) must NOT be flagged — the prior marker-substring heuristic wrongly
    excluded such a fixture and broke test_source_files[java_edge_cases]."""
    f = tmp_path / "UserProfile.java"
    f.write_text("// Java record (auto-generated constructor, accessors, equals)\n" + "x" * 1000)
    assert not _is_generated_source(f)
