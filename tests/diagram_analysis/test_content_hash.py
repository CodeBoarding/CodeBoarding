import hashlib
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, FileEntry, FileMethodGroup, MethodEntry
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.content_hash import MethodRef, MethodSpan, hash_method_body, hash_whole_file, read_source_lines
from diagram_analysis.analysis_json import (
    FileEntryJson,
    MethodIndexEntry,
    _build_file_entry_json_from_files,
    _build_methods_index_from_files,
    _reconstruct_files_index,
    compute_source_tree_hash,
    hash_repo_source_files,
    tree_hash_from_file_hashes,
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
    assert read_source_lines(tmp_path, "nope.py", cache) is None


def test_read_source_lines_reads_and_caches(tmp_path: Path):
    (tmp_path / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    cache: dict[str, list[str] | None] = {}
    assert read_source_lines(tmp_path, "f.py", cache) == ["a", "b", "c"]
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


class _StubMixin(ClusterMethodsMixin):
    """Minimal host for ``build_files_index`` — it only reads ``repo_dir`` and
    ``_live_cfg_method_spans``."""

    def __init__(self, repo_dir: Path, live_spans: dict):
        self.repo_dir = repo_dir
        self._spans = live_spans

    def _live_cfg_method_spans(self):
        return self._spans


def _analysis_with_method(file_path: str, qname: str, start: int, end: int) -> AnalysisInsights:
    return AnalysisInsights(
        description="",
        components=[
            Component(
                name="C",
                description="d",
                key_entities=[],
                component_id="c1",
                file_methods=[
                    FileMethodGroup(
                        file_path=file_path,
                        methods=[
                            MethodEntry(qualified_name=qname, start_line=start, end_line=end, node_type="FUNCTION")
                        ],
                    )
                ],
            )
        ],
        components_relations=[],
    )


def test_build_files_index_uses_live_cfg_span(tmp_path: Path):
    # The method moved down (edit above it); the live CFG knows its real span, so
    # the hash reflects the CURRENT body, not the stale line numbers.
    (tmp_path / "m.py").write_text("# added line\ndef foo():\n    return 1\n", encoding="utf-8")
    analysis = _analysis_with_method("m.py", "foo", start=1, end=2)  # stale carried-forward span
    live = {MethodRef("m.py", "foo"): MethodSpan(2, 3)}  # live CFG span of foo() now
    files = _StubMixin(tmp_path, live).build_files_index(analysis)
    method = files["m.py"].methods[0]
    assert method.content_hash == hash_method_body(["# added line", "def foo():", "    return 1"], 2, 3)
    assert method.content_hash != ""


def test_build_files_index_empty_hash_when_method_absent_from_live_cfg(tmp_path: Path):
    # The carried-forward method is NOT in the live CFG (e.g. deleted/renamed in
    # source). Its stored line numbers may now point at unrelated code, so hashing
    # them would be stable-but-wrong. Expect the ''-unavailable sentinel instead.
    (tmp_path / "m.py").write_text("def something_else():\n    return 42\n", encoding="utf-8")
    analysis = _analysis_with_method("m.py", "foo", start=1, end=2)
    files = _StubMixin(tmp_path, live_spans={}).build_files_index(analysis)
    method = files["m.py"].methods[0]
    assert method.content_hash == ""


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
