import hashlib
from pathlib import Path

from agents.agent_responses import FileEntry, MethodEntry
from agents.cluster_methods_mixin import _read_source_lines, hash_method_body, hash_whole_file
from diagram_analysis.analysis_json import (
    FileEntryJson,
    MethodIndexEntry,
    _build_file_entry_json_from_files,
    _build_methods_index_from_files,
    _compute_source_tree_hash,
    _reconstruct_files_index,
    compute_source_tree_hash,
    hash_repo_source_files,
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


def test_reconstruct_files_index_copies_method_content_hash():
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
    assert hash_method_body(lines, 1, 2) == expected


def test_hash_method_body_empty_on_bad_range():
    assert hash_method_body(["a", "b"], 0, 2) == ""
    assert hash_method_body(["a", "b"], 3, 2) == ""
    assert hash_method_body(None, 1, 2) == ""


def test_read_source_lines_missing_file_returns_none(tmp_path: Path):
    cache: dict[str, list[str] | None] = {}
    assert _read_source_lines(tmp_path, "nope.py", cache) is None


def test_read_source_lines_reads_and_caches(tmp_path: Path):
    (tmp_path / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    cache: dict[str, list[str] | None] = {}
    assert _read_source_lines(tmp_path, "f.py", cache) == ["a", "b", "c"]
    assert "f.py" in cache


def test_file_entry_content_hash_defaults_empty():
    assert FileEntry(methods=[]).content_hash == ""


def test_file_entry_content_hash_roundtrips_value():
    assert FileEntry(methods=[], content_hash="ff00ff00").content_hash == "ff00ff00"


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
    files_raw: dict[str, dict] = {"m.py": {"method_keys": []}}
    files_index = _reconstruct_files_index(files_raw, {})
    assert files_index["m.py"].content_hash == ""


def test_hash_whole_file_hashes_all_lines():
    lines = ["import os", "", "PI = 3.14"]
    expected = hashlib.sha256("import os\n\nPI = 3.14".encode("utf-8")).hexdigest()[:16]
    assert hash_whole_file(lines) == expected


def test_source_tree_hash_stable_and_order_independent():
    a = {"a.py": FileEntry(methods=[], content_hash="11"), "b.py": FileEntry(methods=[], content_hash="22")}
    b = {"b.py": FileEntry(methods=[], content_hash="22"), "a.py": FileEntry(methods=[], content_hash="11")}
    assert _compute_source_tree_hash(a) == _compute_source_tree_hash(b) != ""


def test_source_tree_hash_changes_when_a_file_hash_changes():
    a = {"a.py": FileEntry(methods=[], content_hash="11"), "b.py": FileEntry(methods=[], content_hash="22")}
    b = {"a.py": FileEntry(methods=[], content_hash="11"), "b.py": FileEntry(methods=[], content_hash="33")}
    assert _compute_source_tree_hash(a) != _compute_source_tree_hash(b)


def test_source_tree_hash_empty_when_no_hashes():
    assert _compute_source_tree_hash({"a.py": FileEntry(methods=[])}) == ""


def test_hash_method_body_returns_sentinel_when_span_past_eof():
    # end_line beyond the file must yield '' (unavailable), not a partial-slice hash
    assert hash_method_body(["a", "b"], 1, 5) == ""
    assert hash_method_body(["a", "b"], 3, 4) == ""


def test_hash_repo_source_files_covers_whole_tree(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")

    fps = hash_repo_source_files(tmp_path)
    # Non-code files (README) are included — this is the whole analyzable tree,
    # not just component-assigned code.
    assert set(fps) == {"a.py", "README.md", "pkg/b.py"}
    assert all(len(h) == 16 for h in fps.values())


def test_compute_source_tree_hash_stable_and_change_sensitive(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    first = compute_source_tree_hash(tmp_path)
    assert first == compute_source_tree_hash(tmp_path)  # deterministic

    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert compute_source_tree_hash(tmp_path) != first  # sees the edit


def test_source_tree_hash_reproducible_from_fingerprint_map(tmp_path: Path):
    # The invariant the wrapper relies on: aggregating the per-file fingerprint
    # map reproduces the whole-tree hash byte-for-byte.
    from diagram_analysis.analysis_json import tree_hash_from_file_hashes

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs.md").write_text("hello\n", encoding="utf-8")
    fps = hash_repo_source_files(tmp_path)
    assert tree_hash_from_file_hashes(fps) == compute_source_tree_hash(tmp_path)
