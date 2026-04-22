"""Unit tests for BazelAqueryGenerator — subprocess is mocked with canned
aquery jsonproto output.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.engine.adapters.cpp_cdb import BuildSystemKind, generator_for
from static_analyzer.engine.adapters.cpp_cdb.bazel_generator import (
    BazelAqueryGenerator,
    _find_source_argument,
)
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import compute_fingerprint, write_cached_fingerprint


CANNED_AQUERY_JSON = json.dumps(
    {
        "actions": [
            {
                "mnemonic": "CppCompile",
                "arguments": [
                    "external/toolchain/clang",
                    "-DBAZEL_CURRENT_REPOSITORY=_main",
                    "-Iinclude",
                    "-c",
                    "src/foo.cc",
                    "-o",
                    "bazel-out/k8-fastbuild/bin/_objs/foo/foo.o",
                ],
            },
            {
                "mnemonic": "CppCompile",
                "arguments": [
                    "external/toolchain/clang",
                    "-c",
                    "src/bar.cpp",
                    "-o",
                    "bazel-out/k8-fastbuild/bin/_objs/bar/bar.o",
                ],
            },
            {
                # Not a compile action — filter out.
                "mnemonic": "CppLink",
                "arguments": ["clang", "foo.o", "-o", "bin/app"],
            },
            {
                # Compile action with no recognisable source — drop it.
                "mnemonic": "CppCompile",
                "arguments": ["clang", "--version"],
            },
        ]
    }
)

VALID_CDB_JSON = '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'


def _fake_bazel_version_output() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["bazel", "--version"], returncode=0, stdout="bazel 7.3.1\n", stderr="")


def _fake_bazel_info_exec_root(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["bazel", "info", "execution_root"],
        returncode=0,
        stdout="/private/var/tmp/_bazel_user/abc/execroot/_main\n",
        stderr="",
    )


def _make_fake_run(aquery_stdout: str = CANNED_AQUERY_JSON):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["bazel", "--version"]:
            return _fake_bazel_version_output()
        if argv[:2] == ["bazel", "info"]:
            return _fake_bazel_info_exec_root(Path(kwargs.get("cwd", ".")))
        if argv[:2] == ["bazel", "aquery"]:
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=aquery_stdout, stderr="")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    return fake_run


class TestFindSourceArgument:
    def test_picks_arg_after_dash_c(self) -> None:
        args = ["clang", "-c", "src/x.cc", "-o", "x.o"]
        assert _find_source_argument(args) == "src/x.cc"

    def test_falls_back_to_last_source_suffixed(self) -> None:
        """Bazel toolchains sometimes emit ``--compile src/x.cc`` or similar
        — no ``-c`` pair, but a source file is still in there.
        """
        args = ["clang", "--compile", "src/x.cc", "-o", "x.o"]
        assert _find_source_argument(args) == "src/x.cc"

    def test_none_when_no_source_file(self) -> None:
        assert _find_source_argument(["clang", "--version"]) is None

    def test_ignores_dash_prefixed_args(self) -> None:
        """A flag like ``--foo.c`` must not be misread as a source file."""
        args = ["clang", "-c", "src/x.cc", "-o", "foo.c.d"]
        # `-o foo.c.d` ends in `.d`, not a source suffix — still matches src/x.cc
        assert _find_source_argument(args) == "src/x.cc"

    def test_fallback_ignores_dash_o_output(self) -> None:
        assert _find_source_argument(["clang", "obj.o", "-o", "generated/foo.cc"]) is None

    def test_fallback_skips_output_and_uses_real_source(self) -> None:
        args = ["clang", "src/real.cc", "-o", "generated/foo.cc"]
        assert _find_source_argument(args) == "src/real.cc"


class TestBazelAqueryGeneratorPreflight:
    def test_missing_bazel_raises(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def selective(name):
            return None if name == "bazel" else f"/usr/bin/{name}"

        with patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"bazel.*PATH"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_bazel_too_old_raises(self, tmp_path: Path) -> None:
        """Bazel 5.x's aquery schema differs enough that our parser would
        silently produce a malformed CDB — safer to fail.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def fake_run(argv, **kwargs):
            if argv[:2] == ["bazel", "--version"]:
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="bazel 5.4.0\n", stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"Bazel 5"):
                BazelAqueryGenerator().generate(tmp_path)


class TestBazelAqueryGeneratorHappyPath:
    def test_produces_cdb_from_canned_aquery_output(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=_make_fake_run()
            ),
        ):
            cdb = BazelAqueryGenerator().generate(tmp_path)

        assert cdb.is_file()
        entries = json.loads(cdb.read_text())
        # Two CppCompile actions with extractable source files, one dropped for
        # having no source argument, one dropped for being CppLink.
        assert len(entries) == 2
        by_file = {entry["file"]: entry for entry in entries}
        assert "src/foo.cc" in by_file
        assert "src/bar.cpp" in by_file
        assert by_file["src/foo.cc"]["directory"].endswith("/_main")
        assert by_file["src/foo.cc"]["arguments"][0] == "external/toolchain/clang"

    def test_empty_aquery_raises(self, tmp_path: Path) -> None:
        """If the user's query scope matches no targets, aquery returns an
        empty actions array — treat as a misconfiguration and fail with the
        env var named, not a silent empty CDB.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        empty = json.dumps({"actions": []})

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run",
                side_effect=_make_fake_run(aquery_stdout=empty),
            ),
        ):
            with pytest.raises(RuntimeError, match=r"CODEBOARDING_CPP_BAZEL_QUERY"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_aquery_failure_surfaces_stderr(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def fake_run(argv, **kwargs):
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                return _fake_bazel_info_exec_root(tmp_path)
            if argv[:2] == ["bazel", "aquery"]:
                return subprocess.CompletedProcess(
                    args=argv, returncode=1, stdout="", stderr="ERROR: no such package '@missing_dep'\n"
                )
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"no such package"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_bazel_info_file_not_found_is_runtime_error(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def fake_run(argv, **kwargs):
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                raise FileNotFoundError("bazel")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"command not found"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_bazel_aquery_file_not_found_is_runtime_error(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def fake_run(argv, **kwargs):
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                return _fake_bazel_info_exec_root(tmp_path)
            if argv[:2] == ["bazel", "aquery"]:
                raise FileNotFoundError("bazel")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"command not found"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run",
                side_effect=_make_fake_run(aquery_stdout="not-json{"),
            ),
        ):
            with pytest.raises(RuntimeError, match=r"not valid JSON"):
                BazelAqueryGenerator().generate(tmp_path)


class TestBazelAqueryGeneratorFingerprint:
    """Bazel's ``cc_library`` typically uses ``glob()`` — adding a source
    file changes the build graph without editing any BUILD file, so the
    cached CDB must be invalidated.
    """

    def test_new_cpp_source_changes_fingerprint(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        (tmp_path / "BUILD.bazel").write_text('cc_library(name = "x", srcs = glob(["*.cc"]))\n')
        (tmp_path / "a.cc").write_text("int a() { return 0; }\n")

        from static_analyzer.engine.adapters.cpp_cdb.fingerprint import compute_fingerprint

        before = compute_fingerprint(BazelAqueryGenerator._fingerprint_inputs(tmp_path))
        (tmp_path / "b.cc").write_text("int b() { return 1; }\n")
        after = compute_fingerprint(BazelAqueryGenerator._fingerprint_inputs(tmp_path))
        assert before != after, "new source file must change the Bazel fingerprint"

    def test_files_under_bazel_out_are_ignored(self, tmp_path: Path) -> None:
        """Bazel's ``bazel-out`` / ``bazel-bin`` symlinks point at generated
        output — hashing them would make the cache useless after every build.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        (tmp_path / "bazel-out").mkdir()
        (tmp_path / "bazel-out" / "generated.cc").write_text("// generated\n")
        inputs = BazelAqueryGenerator._fingerprint_inputs(tmp_path)
        assert not any("bazel-out" in str(p) for p in inputs)


class TestBazelAqueryGeneratorCaching:
    def test_fingerprint_inputs_include_nested_build_files(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "BUILD").write_text("cc_library(name='x')\n")
        (tmp_path / "defs.bzl").write_text("X = 1\n")

        inputs = set(BazelAqueryGenerator._fingerprint_inputs(tmp_path))

        assert tmp_path / "MODULE.bazel" in inputs
        assert tmp_path / "pkg" / "BUILD" in inputs
        assert tmp_path / "defs.bzl" in inputs

    def test_cache_hit_skips_bazel(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)

        write_cached_fingerprint(cdb_dir, compute_fingerprint([tmp_path / "MODULE.bazel"]))

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert runs == [], f"cache hit must not invoke bazel, got {runs}"

    def test_invalid_cache_regenerates(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text("[]")
        write_cached_fingerprint(cdb_dir, compute_fingerprint([tmp_path / "MODULE.bazel"]))

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return _make_fake_run()(argv, **kwargs)

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert any(argv[:2] == ["bazel", "aquery"] for argv in runs)

    def test_force_regenerate_ignores_valid_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        write_cached_fingerprint(cdb_dir, compute_fingerprint([tmp_path / "MODULE.bazel"]))
        monkeypatch.setenv("CODEBOARDING_CPP_FORCE_REGENERATE", "1")

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return _make_fake_run()(argv, **kwargs)

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert any(argv[:2] == ["bazel", "aquery"] for argv in runs)


class TestGeneratorForDispatch:
    """Plumbing test: BAZEL kind routes to the Bazel generator."""

    def test_bazel_kind_routes_to_bazel_generator(self) -> None:
        gen = generator_for(BuildSystemKind.BAZEL)
        assert isinstance(gen, BazelAqueryGenerator)
