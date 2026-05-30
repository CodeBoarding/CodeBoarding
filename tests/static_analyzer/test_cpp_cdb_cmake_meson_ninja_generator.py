"""Tests for the native CMake / Meson / Ninja CDB generators."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.cdb import (
    BuildSystemKind,
    CMakeGenerator,
    MesonGenerator,
    NinjaGenerator,
    generator_for,
)
from static_analyzer.cdb.base import CDB_SUBDIR

VALID_CDB_JSON = json.dumps([{"directory": "/p", "file": "a.cpp", "arguments": ["c++", "-c", "a.cpp"]}])


class TestGeneratorForResolution:
    def test_cmake_resolves(self) -> None:
        assert isinstance(generator_for(BuildSystemKind.CMAKE), CMakeGenerator)

    def test_meson_resolves(self) -> None:
        assert isinstance(generator_for(BuildSystemKind.MESON), MesonGenerator)

    def test_ninja_resolves(self) -> None:
        assert isinstance(generator_for(BuildSystemKind.NINJA), NinjaGenerator)


class TestCmakeGenerator:
    def test_runs_cmake_with_export_flag(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "main.cpp").write_text("int main(){}\n")

        recorded: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            recorded.append(list(argv))
            if argv[0] == "cmake":
                # Side-effect: emit CDB into the configured build dir.
                bdir = Path(argv[argv.index("-B") + 1])
                bdir.mkdir(parents=True, exist_ok=True)
                (bdir / "compile_commands.json").write_text(VALID_CDB_JSON)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            cdb = CMakeGenerator().generate(tmp_path)

        assert cdb.is_file()
        cmake_argv = next(a for a in recorded if a and a[0] == "cmake")
        assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in cmake_argv
        assert "-S" in cmake_argv and "-B" in cmake_argv
        # Build dir is under the hidden CDB subdir, never the user's `build/`.
        bdir = Path(cmake_argv[cmake_argv.index("-B") + 1])
        assert str(CDB_SUBDIR) in str(bdir)

    def test_failure_surfaces_stderr(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("garbage(\n")
        (tmp_path / "main.cpp").write_text("int main(){}\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="syntax error")

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match=r"(?s)cmake configure failed.*syntax error"):
                CMakeGenerator().generate(tmp_path)


class TestMesonGenerator:
    def test_runs_meson_setup(self, tmp_path: Path) -> None:
        (tmp_path / "meson.build").write_text("project('x', 'cpp')\n")
        (tmp_path / "main.cpp").write_text("int main(){}\n")

        recorded: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            recorded.append(list(argv))
            if argv[:2] == ["meson", "setup"]:
                bdir = Path(argv[2])
                bdir.mkdir(parents=True, exist_ok=True)
                (bdir / "compile_commands.json").write_text(VALID_CDB_JSON)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            cdb = MesonGenerator().generate(tmp_path)

        assert cdb.is_file()
        meson_argv = next(a for a in recorded if a[:2] == ["meson", "setup"])
        assert "--reconfigure" in meson_argv


class TestNinjaGenerator:
    def test_parses_compdb_stdout(self, tmp_path: Path) -> None:
        (tmp_path / "build.ninja").write_text("rule cc\n")
        (tmp_path / "main.cpp").write_text("int main(){}\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            assert argv == ["ninja", "-t", "compdb"]
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=VALID_CDB_JSON, stderr="")

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            cdb = NinjaGenerator().generate(tmp_path)

        assert cdb.is_file()

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "build.ninja").write_text("rule cc\n")
        (tmp_path / "main.cpp").write_text("int main(){}\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="not-json", stderr="")

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match=r"invalid JSON"):
                NinjaGenerator().generate(tmp_path)
