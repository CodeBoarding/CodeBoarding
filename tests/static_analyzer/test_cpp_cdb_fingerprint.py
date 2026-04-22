"""Tests for the fingerprint-based CDB cache helper."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.fingerprint import (
    compute_fingerprint,
    delete_cached_fingerprint,
    read_cached_fingerprint,
    write_cached_fingerprint,
)


class TestComputeFingerprint:
    def test_empty_inputs_is_deterministic(self) -> None:
        assert compute_fingerprint([]) == compute_fingerprint([])

    def test_changes_when_content_changes(self, tmp_path: Path) -> None:
        f = tmp_path / "Makefile"
        f.write_text("all:\n\techo hi\n")
        before = compute_fingerprint([f])
        f.write_text("all:\n\techo bye\n")
        after = compute_fingerprint([f])
        assert before != after

    def test_changes_when_file_deleted(self, tmp_path: Path) -> None:
        f = tmp_path / "Makefile"
        f.write_text("all:\n")
        present = compute_fingerprint([f])
        f.unlink()
        missing = compute_fingerprint([f])
        assert present != missing

    def test_order_independent(self, tmp_path: Path) -> None:
        """Fingerprint must be stable regardless of iteration order —
        otherwise a dict-shuffled input list spuriously busts the cache.
        """
        a = tmp_path / "a.mk"
        b = tmp_path / "b.mk"
        a.write_text("A")
        b.write_text("B")
        assert compute_fingerprint([a, b]) == compute_fingerprint([b, a])

    def test_path_contributes_to_hash(self, tmp_path: Path) -> None:
        """Same content at a different path must hash differently — two
        files with identical bytes are still distinct inputs and should
        invalidate the cache on rename.
        """
        a = tmp_path / "a.mk"
        b = tmp_path / "b.mk"
        a.write_text("same")
        b.write_text("same")
        assert compute_fingerprint([a]) != compute_fingerprint([b])


class TestCachedFingerprintRoundTrip:
    def test_none_when_marker_missing(self, tmp_path: Path) -> None:
        assert read_cached_fingerprint(tmp_path) is None

    def test_round_trip(self, tmp_path: Path) -> None:
        write_cached_fingerprint(tmp_path, "deadbeef")
        assert read_cached_fingerprint(tmp_path) == "deadbeef"

    def test_overwrites_previous(self, tmp_path: Path) -> None:
        write_cached_fingerprint(tmp_path, "first")
        write_cached_fingerprint(tmp_path, "second")
        assert read_cached_fingerprint(tmp_path) == "second"

    def test_creates_dir_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "does" / "not" / "exist"
        write_cached_fingerprint(target, "cafebabe")
        assert read_cached_fingerprint(target) == "cafebabe"

    def test_empty_marker_reads_as_none(self, tmp_path: Path) -> None:
        (tmp_path / ".fingerprint").write_text("")
        assert read_cached_fingerprint(tmp_path) is None

    def test_delete_removes_marker_and_temp_files(self, tmp_path: Path) -> None:
        write_cached_fingerprint(tmp_path, "deadbeef")
        (tmp_path / "..fingerprint.leftover.tmp").write_text("partial")
        delete_cached_fingerprint(tmp_path)
        assert read_cached_fingerprint(tmp_path) is None
        assert not list(tmp_path.glob(".*fingerprint.*.tmp"))
