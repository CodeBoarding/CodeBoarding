"""Tests for synthesized fallback flags (header-only / empty-CDB projects)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from static_analyzer.cdb import CDB_SUBDIR, resolve_cdb, synthesize_fallback_flags
from static_analyzer.cdb import fallback_flags as ff


def _read_flags(project_root: Path) -> list[str]:
    return (project_root / CDB_SUBDIR / "compile_flags.txt").read_text().splitlines()


class TestSynthesizeFallbackFlags:

    def test_writes_std_and_absolute_root_include(self, tmp_path: Path) -> None:
        out_dir = synthesize_fallback_flags(tmp_path)
        assert out_dir == tmp_path / CDB_SUBDIR
        flags = _read_flags(tmp_path)
        assert "-std=c++20" in flags
        assert f"-I{tmp_path.resolve()}" in flags

    def test_discovers_nested_include_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "libfoo" / "include").mkdir(parents=True)
        (tmp_path / "inc").mkdir()
        synthesize_fallback_flags(tmp_path)
        flags = _read_flags(tmp_path)
        assert f"-I{(tmp_path / 'libfoo' / 'include').resolve()}" in flags
        assert f"-I{(tmp_path / 'inc').resolve()}" in flags

    def test_skips_build_and_hidden_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "build" / "include").mkdir(parents=True)
        (tmp_path / ".cache" / "include").mkdir(parents=True)
        synthesize_fallback_flags(tmp_path)
        flags = _read_flags(tmp_path)
        assert f"-I{(tmp_path / 'build' / 'include').resolve()}" not in flags
        assert f"-I{(tmp_path / '.cache' / 'include').resolve()}" not in flags

    def test_cuda_stubs_only_when_cuda_sources_present(self, tmp_path: Path) -> None:
        synthesize_fallback_flags(tmp_path)
        assert "-D__device__=" not in _read_flags(tmp_path)
        (tmp_path / "kernels.cuh").write_text("")
        synthesize_fallback_flags(tmp_path)
        assert "-D__device__=" in _read_flags(tmp_path)
        assert "-D__host__=" in _read_flags(tmp_path)

    def test_returns_none_when_write_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        assert synthesize_fallback_flags(tmp_path) is None

    def test_include_discovery_is_depth_bounded(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "include"
        deep.mkdir(parents=True)
        synthesize_fallback_flags(tmp_path)
        assert f"-I{deep.resolve()}" not in _read_flags(tmp_path)

    def test_include_root_cap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ff, "_MAX_INCLUDE_ROOTS", 3)
        for i in range(6):
            (tmp_path / f"mod{i}" / "include").mkdir(parents=True)
        synthesize_fallback_flags(tmp_path)
        assert sum(1 for f in _read_flags(tmp_path) if f.startswith("-I")) <= 4  # root + cap


class TestResolveCdbFallback:
    """Header-only repos (cccl-shaped: cmake configure succeeds, CDB is empty)
    must analyze with synthesized flags instead of refusing."""

    def test_no_cdb_no_markers_falls_back(self, tmp_path: Path) -> None:
        resolution = resolve_cdb(tmp_path)
        assert resolution.is_fallback
        assert resolution.cdb_dir == tmp_path / CDB_SUBDIR
        assert (resolution.cdb_dir / "compile_flags.txt").is_file()

    def test_empty_user_cdb_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "compile_commands.json").write_text("")
        resolution = resolve_cdb(tmp_path)
        assert resolution.is_fallback
        assert resolution.cdb_dir == tmp_path / CDB_SUBDIR

    def test_valid_user_cdb_wins_over_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(
            json.dumps([{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}])
        )
        resolution = resolve_cdb(tmp_path)
        assert not resolution.is_fallback
        assert resolution.cdb_dir == tmp_path

    def test_user_compile_flags_wins_over_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++17\n")
        resolution = resolve_cdb(tmp_path)
        assert not resolution.is_fallback
        assert resolution.cdb_dir == tmp_path

    def test_fallback_warns_once_per_invalid_user_cdb(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """resolve_cdb used to re-run detection inside ensure_cdb, duplicating
        every 'Ignoring user compile_commands.json' warning."""
        (tmp_path / "compile_commands.json").write_text("")
        with caplog.at_level(logging.WARNING, logger="static_analyzer.cdb.detect"):
            resolve_cdb(tmp_path)
        hits = [r for r in caplog.records if "Ignoring user compile_commands.json" in r.message]
        assert len(hits) == 1

    def test_fallback_emits_degraded_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="static_analyzer.cdb"):
            resolve_cdb(tmp_path)
        assert any("synthesized fallback flags" in r.message for r in caplog.records)

    def test_synthesis_failure_yields_none_with_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import static_analyzer.cdb as cdb_pkg

        monkeypatch.setattr(cdb_pkg, "synthesize_fallback_flags", lambda root: None)
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir is None
        assert resolution.error_hint
