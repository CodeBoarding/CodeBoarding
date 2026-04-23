"""Tests for ``CppAdapter.prepare_project`` — the dispatcher between
``detect_build_system`` and the per-build-system CDB generators.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.engine.adapters.cpp_adapter import CppAdapter
from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind


class TestPrepareProjectSkipConditions:
    """We must skip generation when it'd be wasteful or unwanted."""

    def test_skip_when_user_cdb_already_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user-owned compile_commands.json short-circuits even with opt-in
        set — re-running 'make' on a repo whose CDB the user committed is
        pure wasted time, and we don't want to clobber their file.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "compile_commands.json").write_text("[]")
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for") as gen_for:
            CppAdapter().prepare_project(tmp_path)
        gen_for.assert_not_called()

    def test_generated_cdb_does_not_short_circuit_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale CDB under ``.codeboarding/cdb/`` must NOT skip the
        generator — the generator owns the fingerprint cache and is the
        only thing that can decide "reuse" vs "rebuild". If
        ``prepare_project`` short-circuited here, editing the Makefile
        would never refresh the CDB.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text('[{"directory": ".", "file": "x.cc", "command": "c++"}]')

        fake_generator = MagicMock()
        fake_generator.generate.return_value = cdb_dir / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path)

    def test_skip_when_optin_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without the env var we never touch the user's repo — even if we
        detect a Makefile we'd know how to handle.
        """
        monkeypatch.delenv("CODEBOARDING_CPP_GENERATE_CDB", raising=False)
        (tmp_path / "Makefile").write_text("all:\n")
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for") as gen_for:
            CppAdapter().prepare_project(tmp_path)
        gen_for.assert_not_called()

    def test_skip_when_kind_has_no_generator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CMake / Meson / Ninja hit this branch — their CLI is trivial, so
        we don't auto-run. The user already sees a useful hint from
        ``get_lsp_command``.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        CppAdapter().prepare_project(tmp_path)  # Must not raise
        # And no CDB magically appeared
        assert not (tmp_path / ".codeboarding" / "cdb" / "compile_commands.json").is_file()


class TestPrepareProjectInvokesBearForMake:
    def test_make_project_calls_bear_generator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.return_value = tmp_path / ".codeboarding" / "cdb" / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path)

    def test_generator_failure_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing generator must log but not raise — ``get_lsp_command``
        owns the user-facing error surface, and we don't want prepare_project
        crashes to obscure that clearer message.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.side_effect = RuntimeError("make exploded")
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)  # Must NOT raise
        assert "CDB generation failed" in caplog.text
