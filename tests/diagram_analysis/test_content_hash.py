import hashlib
import json
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from agents.file_index_models import FileEntry, MethodEntry
from agents.content_hash import (
    compute_source_tree_hash,
    hash_method_body,
    hash_repo_source_files,
    hash_whole_file,
    read_source_lines,
    tree_hash_from_file_hashes,
)
from diagram_analysis.analysis_json import build_unified_analysis_json, parse_unified_analysis


def test_method_entry_content_hash_defaults_empty():
    m = MethodEntry(qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION")
    assert m.content_hash == ""


def test_method_entry_content_hash_roundtrips_value():
    m = MethodEntry(qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION", content_hash="deadbeef")
    assert m.content_hash == "deadbeef"


def test_stored_analysis_keeps_file_and_method_content_hashes():
    analysis = AnalysisInsights(description="d", components=[], components_relations=[])
    analysis.files = {
        "m.py": FileEntry(
            methods=[
                MethodEntry(
                    qualified_name="m.foo", start_line=1, end_line=2, node_type="FUNCTION", content_hash="abc123"
                )
            ],
            content_hash="ff00",
        )
    }
    stored = build_unified_analysis_json(analysis, [], "repo", repo_dir=Path("."), source_tree_hash="", depth_cap=1)

    parsed, _ = parse_unified_analysis(json.loads(stored))

    assert parsed.files["m.py"].content_hash == "ff00"
    assert parsed.files["m.py"].methods[0].content_hash == "abc123"


def test_hash_method_body_hashes_line_range():
    lines = ["def foo():", "    return 1", "", "def bar():", "    return 2"]
    expected = hashlib.sha256("def foo():\n    return 1".encode("utf-8")).hexdigest()[:16]
    assert hash_method_body(lines, 1, 2) == expected


def test_hash_method_body_empty_on_bad_range():
    assert hash_method_body(["a", "b"], 0, 2) == ""
    assert hash_method_body(["a", "b"], 3, 2) == ""
    assert hash_method_body([], 1, 2) == ""


def test_read_source_lines_missing_file_returns_empty(tmp_path: Path):
    cache: dict[str, list[str]] = {}
    assert read_source_lines(tmp_path, "nope.py", cache) == []


def test_read_source_lines_reads_and_caches(tmp_path: Path):
    (tmp_path / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    cache: dict[str, list[str]] = {}
    assert read_source_lines(tmp_path, "f.py", cache) == ["a", "b", "c"]
    assert "f.py" in cache


def test_file_entry_content_hash_defaults_empty():
    assert FileEntry(methods=[]).content_hash == ""


def test_file_entry_content_hash_roundtrips_value():
    assert FileEntry(methods=[], content_hash="ff00ff00").content_hash == "ff00ff00"


def test_hash_whole_file_hashes_all_lines():
    lines = ["import os", "", "PI = 3.14"]
    expected = hashlib.sha256("import os\n\nPI = 3.14".encode("utf-8")).hexdigest()[:16]
    assert hash_whole_file(lines) == expected


def test_tree_hash_stable_and_order_independent():
    a = {"a.py": "11", "b.py": "22"}
    b = {"b.py": "22", "a.py": "11"}
    assert tree_hash_from_file_hashes(a) == tree_hash_from_file_hashes(b) != ""


def test_tree_hash_changes_when_a_file_hash_changes():
    a = {"a.py": "11", "b.py": "22"}
    b = {"a.py": "11", "b.py": "33"}
    assert tree_hash_from_file_hashes(a) != tree_hash_from_file_hashes(b)


def test_tree_hash_empty_when_no_hashes():
    assert tree_hash_from_file_hashes({"a.py": ""}) == ""


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
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs.md").write_text("hello\n", encoding="utf-8")
    fps = hash_repo_source_files(tmp_path)
    assert tree_hash_from_file_hashes(fps) == compute_source_tree_hash(tmp_path)


def test_invalid_utf8_bytes_do_not_collide(tmp_path: Path):
    # Two files differing ONLY in an invalid UTF-8 byte must hash differently.
    # With errors='replace' both bytes fold to U+FFFD and the hashes collide,
    # silently masking a real change; surrogateescape keeps them distinct.
    (tmp_path / "a.bin").write_bytes(b"x\xff\n")
    (tmp_path / "b.bin").write_bytes(b"x\x80\n")
    fps = hash_repo_source_files(tmp_path)
    assert fps["a.bin"] != fps["b.bin"]


def test_invalid_utf8_whole_file_hash_distinct():
    a = "x\udcff".splitlines()  # decoded via surrogateescape from b'x\xff'
    b = "x\udc80".splitlines()  # decoded via surrogateescape from b'x\x80'
    assert hash_whole_file(a) != hash_whole_file(b)
