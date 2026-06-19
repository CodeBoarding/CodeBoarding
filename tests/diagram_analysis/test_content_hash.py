import hashlib
from pathlib import Path

from agents.analysis_models import FileEntry, MethodEntry
from agents.analysis_models import FileEntry as _FileEntry
from agents.cluster_methods_mixin import _hash_method_body, _hash_whole_file, _read_source_lines
from diagram_analysis.analysis_json import (
    FileEntryJson,
    MethodIndexEntry,
    _build_file_entry_json_from_files,
    _build_methods_index_from_files,
    _reconstruct_files_index,
)


def test_method_entry_content_hash_defaults_empty():
    m = MethodEntry(qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION")
    assert m.content_hash == ""


def test_method_entry_content_hash_roundtrips_value():
    m = MethodEntry(qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION", content_hash="deadbeef")
    assert m.content_hash == "deadbeef"


def test_method_index_entry_content_hash_defaults_empty():
    e = MethodIndexEntry(file_path="m.py", qualified_name="m.foo", start_line=1, end_line=2, type="FUNCTION")
    assert e.content_hash == ""


def test_build_methods_index_copies_content_hash():
    files_index = {
        "m.py": FileEntry(
            methods=[
                MethodEntry(
                    qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION", content_hash="abc123"
                )
            ]
        )
    }
    idx = _build_methods_index_from_files(files_index)
    assert idx["m.py|m.foo"].content_hash == "abc123"


def test_reconstruct_files_index_copies_content_hash():
    methods_index = {
        "m.py|m.foo": MethodIndexEntry(
            file_path="m.py", qualified_name="m.foo", start_line=1, end_line=2, type="FUNCTION", content_hash="abc123"
        )
    }
    files_raw = {"m.py": {"method_keys": ["m.py|m.foo"]}}
    files_index = _reconstruct_files_index(files_raw, methods_index)
    assert files_index["m.py"].methods[0].content_hash == "abc123"


def test_hash_method_body_hashes_line_range():
    lines = ["def foo():", "    return 1", "", "def bar():", "    return 2"]
    expected = hashlib.sha256("def foo():\n    return 1".encode("utf-8")).hexdigest()[:16]
    assert _hash_method_body(lines, 1, 2) == expected


def test_hash_method_body_empty_on_bad_range():
    assert _hash_method_body(["a", "b"], 0, 2) == ""
    assert _hash_method_body(["a", "b"], 3, 2) == ""
    assert _hash_method_body(None, 1, 2) == ""
    # end_line past the file end (e.g. file truncated since lines were recorded)
    assert _hash_method_body(["a", "b"], 1, 5) == ""
    assert _hash_method_body(["a", "b"], 3, 5) == ""


def test_read_source_lines_missing_file_returns_none(tmp_path: Path):
    cache: dict[str, list[str] | None] = {}
    assert _read_source_lines(tmp_path, "nope.py", cache) is None


def test_read_source_lines_reads_and_caches(tmp_path: Path):
    (tmp_path / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    cache: dict[str, list[str] | None] = {}
    assert _read_source_lines(tmp_path, "f.py", cache) == ["a", "b", "c"]
    assert "f.py" in cache


def test_file_entry_content_hash_defaults_empty():
    assert _FileEntry(methods=[]).content_hash == ""


def test_file_entry_content_hash_roundtrips_value():
    assert _FileEntry(methods=[], content_hash="ff00ff00").content_hash == "ff00ff00"


def test_file_entry_json_content_hash_defaults_empty():
    assert FileEntryJson(method_keys=[]).content_hash == ""


def test_file_entry_json_build_copies_content_hash():
    files_index = {"m.py": FileEntry(methods=[], content_hash="abc123")}
    out = _build_file_entry_json_from_files(files_index)
    assert out["m.py"].content_hash == "abc123"


def test_reconstruct_files_index_restores_file_content_hash():
    files_raw = {"m.py": {"method_keys": [], "content_hash": "abc123"}}
    files_index = _reconstruct_files_index(files_raw, {})
    assert files_index["m.py"].content_hash == "abc123"


def test_reconstruct_files_index_missing_file_hash_defaults_empty():
    files_raw = {"m.py": {"method_keys": []}}
    files_index = _reconstruct_files_index(files_raw, {})
    assert files_index["m.py"].content_hash == ""


def test_hash_whole_file_hashes_all_lines():
    lines = ["import os", "", "PI = 3.14"]
    expected = hashlib.sha256("import os\n\nPI = 3.14".encode("utf-8")).hexdigest()[:16]
    assert _hash_whole_file(lines) == expected


def test_hash_whole_file_none_is_empty():
    assert _hash_whole_file(None) == ""


def test_hash_whole_file_empty_is_hash_of_empty_not_sentinel():
    assert _hash_whole_file([]) == hashlib.sha256(b"").hexdigest()[:16]
    assert _hash_whole_file([]) != ""
