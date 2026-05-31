"""Tests for ``resolve_cdb`` and ``CdbResolution.needs_compile_commands_dir``.

HIGH#3: ``cpp_adapter.get_lsp_command`` must rely on an explicit boolean
field rather than path-identity (``cdb_dir != project_root``) so future
changes that resolve or symlink the path can't silently flip the branch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.cdb import CdbResolution, resolve_cdb
from static_analyzer.cdb.base import CDB_SUBDIR
from static_analyzer.cdb.detect import DetectionResult

_VALID_CDB = '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'


class TestResolveCdbNeedsCompileCommandsDir:
    def test_user_cdb_at_root_needs_no_compile_commands_dir(self, tmp_path: Path) -> None:
        """User CDB at the project root: clangd's walk-up search finds it."""
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir == tmp_path
        assert resolution.needs_compile_commands_dir is False

    def test_user_compile_flags_at_root_needs_no_compile_commands_dir(self, tmp_path: Path) -> None:
        """``compile_flags.txt`` at the root is equally walk-up-discoverable."""
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir == tmp_path
        assert resolution.needs_compile_commands_dir is False

    def test_user_cdb_in_subdir_needs_compile_commands_dir(self, tmp_path: Path) -> None:
        """User CDB in a subdir (``src/``, ``build/``) must surface the flag
        so sibling-dir sources index against the same CDB."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "compile_commands.json").write_text(_VALID_CDB)
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir == tmp_path / "src"
        assert resolution.needs_compile_commands_dir is True

    def test_user_cdb_in_build_needs_compile_commands_dir(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text(_VALID_CDB)
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir == tmp_path / "build"
        assert resolution.needs_compile_commands_dir is True

    def test_generated_cdb_needs_compile_commands_dir(self, tmp_path: Path) -> None:
        """Generated CDB lives at ``.codeboarding/cdb/``; clangd's walk-up
        from source files never visits it, so the flag is mandatory.
        """
        cdb_dir = tmp_path / CDB_SUBDIR
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text(_VALID_CDB)
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir == cdb_dir
        assert resolution.needs_compile_commands_dir is True

    def test_no_cdb_returns_none_and_field_is_default(self, tmp_path: Path) -> None:
        """No usable CDB: ``cdb_dir`` is ``None`` and the boolean defaults to
        ``False`` (callers should branch on ``cdb_dir is None`` first)."""
        resolution = resolve_cdb(tmp_path)
        assert resolution.cdb_dir is None
        assert resolution.needs_compile_commands_dir is False
        assert resolution.error_hint is not None

    def test_resolution_is_frozen_dataclass(self) -> None:
        """``CdbResolution`` must stay immutable so adapters can cache it."""
        from dataclasses import FrozenInstanceError

        resolution = CdbResolution(cdb_dir=None, detection=resolve_cdb(Path("/nonexistent")).detection)
        with pytest.raises(FrozenInstanceError):
            resolution.needs_compile_commands_dir = True  # type: ignore[misc]


class TestResolveCdbGeneratedPathBoolean:
    """When generation runs and produces a CDB, ``needs_compile_commands_dir``
    must be ``True`` even if the implementation later resolves the path."""

    def test_generated_cdb_via_ensure_cdb_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub ``ensure_cdb`` to materialise a CDB, then verify the boolean."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")

        def _fake_ensure(root: Path, detection: DetectionResult | None = None) -> Path | None:
            cdb_dir = root / CDB_SUBDIR
            cdb_dir.mkdir(parents=True, exist_ok=True)
            (cdb_dir / "compile_commands.json").write_text(_VALID_CDB)
            return cdb_dir / "compile_commands.json"

        with patch("static_analyzer.cdb.ensure_cdb", side_effect=_fake_ensure):
            resolution = resolve_cdb(tmp_path)
        assert resolution.needs_compile_commands_dir is True
        assert resolution.cdb_dir == tmp_path / CDB_SUBDIR


class TestResolveCdbRunsDetectBuildSystemOnce:
    """HIGH#4: ``resolve_cdb`` previously called ``detect_build_system`` and
    then ``ensure_cdb`` (which re-detected internally), doubling the
    ``_PROBE_SUBDIRS`` filesystem walk per analysis. The fix threads the
    already-computed ``DetectionResult`` into ``ensure_cdb``.
    """

    def test_resolve_cdb_runs_detect_build_system_once_user_cdb_path(self, tmp_path: Path) -> None:
        """User-CDB short-circuit: ``ensure_cdb`` isn't called at all, so
        detection still runs exactly once."""
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        with patch(
            "static_analyzer.cdb.detect_build_system",
            wraps=__import__("static_analyzer.cdb.detect", fromlist=["detect_build_system"]).detect_build_system,
        ) as mock_detect:
            resolve_cdb(tmp_path)
        assert mock_detect.call_count == 1

    def test_resolve_cdb_runs_detect_build_system_once_generated_path(self, tmp_path: Path) -> None:
        """Generated-CDB path: ``resolve_cdb`` calls detection, then
        ``ensure_cdb`` must reuse the result instead of re-detecting."""
        from static_analyzer.cdb import detect as detect_mod

        with (
            patch(
                "static_analyzer.cdb.detect_build_system",
                wraps=detect_mod.detect_build_system,
            ) as mock_detect,
            patch(
                "static_analyzer.cdb.ensure_cdb",
                return_value=None,
            ),
        ):
            resolve_cdb(tmp_path)
        assert mock_detect.call_count == 1

    def test_resolve_cdb_passes_detection_into_ensure_cdb(self, tmp_path: Path) -> None:
        """The detection result must be threaded through so ``ensure_cdb``
        doesn't repeat the probe walk on its own."""
        sentinel: list[DetectionResult | None] = []

        def _capture(root: Path, detection: DetectionResult | None = None) -> Path | None:
            sentinel.append(detection)
            return None

        with patch("static_analyzer.cdb.ensure_cdb", side_effect=_capture):
            resolve_cdb(tmp_path)
        assert len(sentinel) == 1
        assert sentinel[0] is not None
