"""Unit tests for BazelAqueryGenerator (subprocess mocked)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.cdb import BuildSystemKind, config, generator_for
from static_analyzer.cdb.base import CdbGenerator, run_build_step
from static_analyzer.cdb.bazel_generator import (
    BazelAqueryGenerator,
    _find_source_argument,
)
from static_analyzer.cdb.bear_generator import BearGenerator
from static_analyzer.cdb.fingerprint import compute_fingerprint, write_cached_fingerprint


def _bazel_fingerprint(paths: list[Path]) -> str:
    """Compute the fingerprint a Bazel ``generate()`` call would store.

    Why: M9 — ``CdbGenerator.generate`` now folds ``self.kind`` and
    ``config.fingerprint_options()`` into the digest. Tests that pre-seed
    the cache for a Bazel generator must do the same or every subsequent
    invocation is a spurious cache miss.
    """
    metadata = [("__kind__", BuildSystemKind.BAZEL.value), *config.fingerprint_options()]
    return compute_fingerprint(paths, metadata=metadata)


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
        args = ["clang", "--compile", "src/x.cc", "-o", "x.o"]
        assert _find_source_argument(args) == "src/x.cc"

    def test_none_when_no_source_file(self) -> None:
        assert _find_source_argument(["clang", "--version"]) is None

    def test_ignores_dash_prefixed_args(self) -> None:
        args = ["clang", "-c", "src/x.cc", "-o", "foo.c.d"]
        assert _find_source_argument(args) == "src/x.cc"

    def test_fallback_ignores_dash_o_output(self) -> None:
        assert _find_source_argument(["clang", "obj.o", "-o", "generated/foo.cc"]) is None

    def test_fallback_skips_output_and_uses_real_source(self) -> None:
        args = ["clang", "src/real.cc", "-o", "generated/foo.cc"]
        assert _find_source_argument(args) == "src/real.cc"

    def test_find_source_argument_treats_trailing_output_flag_correctly(self) -> None:
        """A dangling ``-o`` at the end of argv used to leak the preceding
        ``.cc`` path back as a source because ``args[:-1]`` skipped the flag
        in the forward pass while the reverse scan still walked the whole
        list. Consistent enumeration must not return the output-looking path.
        """
        assert _find_source_argument(["clang", "generated/foo.cc", "-o"]) is None

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            # Trailing --output behaves the same as trailing -o.
            (["clang", "generated/foo.cc", "--output"], None),
            # Trailing -o with no preceding source-looking arg -> None.
            (["clang", "-o"], None),
            # -o earlier, value present -> fallback picks the real source.
            (["clang", "src/real.cc", "-o", "out.o"], "src/real.cc"),
            # Output path also has a source suffix — skip it, return the real source.
            (["clang", "src/a.cc", "-o", "generated/b.cc"], "src/a.cc"),
        ],
    )
    def test_find_source_argument_argv_shapes(self, args: list[str], expected: str | None) -> None:
        assert _find_source_argument(args) == expected


class TestBazelAqueryGeneratorPreflight:
    def test_missing_bazel_raises(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def selective(name):
            return None if name == "bazel" else f"/usr/bin/{name}"

        with patch("static_analyzer.cdb.bazel_generator.shutil.which", side_effect=selective):
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
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"Bazel 5"):
                BazelAqueryGenerator().generate(tmp_path)


class TestBazelAqueryGeneratorHappyPath:
    def test_produces_cdb_from_canned_aquery_output(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=_make_fake_run()),
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
        # Entries are rooted at the workspace; execroot-only paths (external
        # toolchains, bazel-out artifacts) are absolutized against the execroot.
        assert by_file["src/foo.cc"]["directory"] == str(tmp_path)
        assert (
            by_file["src/foo.cc"]["arguments"][0]
            == "/private/var/tmp/_bazel_user/abc/execroot/_main/external/toolchain/clang"
        )

    def test_empty_aquery_raises(self, tmp_path: Path) -> None:
        """If the user's query scope matches no targets, aquery returns an
        empty actions array — treat as a misconfiguration and fail with the
        env var named, not a silent empty CDB.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        empty = json.dumps({"actions": []})

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bazel_generator.subprocess.run",
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
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run),
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
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run),
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
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"command not found"):
                BazelAqueryGenerator().generate(tmp_path)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bazel_generator.subprocess.run",
                side_effect=_make_fake_run(aquery_stdout="not-json{"),
            ),
        ):
            with pytest.raises(RuntimeError, match=r"not valid JSON"):
                BazelAqueryGenerator().generate(tmp_path)


class TestActionsToCdbMnemonicFiltering:
    """Non-compile actions must be dropped even when the mnemonic field is
    missing, None, or a non-string — failing open would produce nonsense CDB
    entries from link/test actions.
    """

    def test_mnemonic_none_is_skipped(self) -> None:
        aquery = {
            "actions": [
                {"mnemonic": None, "arguments": ["clang", "-c", "src/x.cc"]},
            ]
        }
        assert BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws")) == []

    def test_missing_mnemonic_keys_skipped(self) -> None:
        aquery = {
            "actions": [
                {"arguments": ["clang", "-c", "src/x.cc"]},
            ]
        }
        assert BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws")) == []

    def test_actions_to_cdb_resolves_mnemonic_id_via_mnemonics_table(self) -> None:
        """Bazel 8+ jsonproto encodes mnemonics as integer ids into a top-level
        ``mnemonics`` table — the old ``isinstance(mnemonic, str)`` check would
        silently drop compile actions in that schema.
        """
        aquery = {
            "mnemonics": [
                {"id": 7, "label": "CppCompile"},
                {"id": 8, "label": "CppLink"},
            ],
            "actions": [
                {
                    "mnemonicId": 7,
                    "arguments": ["clang", "-c", "src/x.cc", "-o", "x.o"],
                },
                {
                    "mnemonicId": 8,
                    "arguments": ["clang", "x.o", "-o", "bin/app"],
                },
            ],
        }
        entries = BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws"))
        assert len(entries) == 1
        assert entries[0]["file"] == "src/x.cc"

    def test_actions_to_cdb_ignores_unmapped_mnemonic_id(self) -> None:
        """If ``mnemonicId`` references an id not in the table, drop the action
        rather than ship a CDB entry with a missing mnemonic.
        """
        aquery = {
            "mnemonics": [{"id": 7, "label": "CppCompile"}],
            "actions": [
                {"mnemonicId": 99, "arguments": ["clang", "-c", "src/x.cc"]},
            ],
        }
        assert BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws")) == []

    def test_objc_compile_mnemonic_is_emitted(self) -> None:
        """``ObjcCompile`` ends in ``Compile`` — verify the wildcard matches
        Objective-C actions (relied on by the source docstring).
        """
        aquery = {
            "actions": [
                {
                    "mnemonic": "ObjcCompile",
                    "arguments": ["clang", "-c", "src/x.m", "-o", "x.o"],
                },
            ]
        }
        entries = BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws"))
        assert len(entries) == 1
        assert entries[0]["file"] == "src/x.m"

    def test_cpp_module_compile_mnemonic_is_emitted(self) -> None:
        """C++20 module units ship as ``CppModuleCompile``."""
        aquery_no_recognised_source = {
            "actions": [
                {
                    "mnemonic": "CppModuleCompile",
                    "arguments": ["clang", "-c", "src/mod.cppm", "-o", "mod.pcm"],
                },
            ]
        }
        # ``.cppm`` isn't currently in ``_SOURCE_SUFFIXES`` — the action lands
        # in the table iff ``arguments`` carries a recognisable source.
        # Use ``-c`` + a ``.cc`` companion to keep the test focused on mnemonic
        # filtering rather than suffix handling.
        aquery_with_cc = {
            "actions": [
                {
                    "mnemonic": "CppModuleCompile",
                    "arguments": ["clang", "-c", "src/mod.cc", "-o", "mod.pcm"],
                },
            ]
        }
        entries = BazelAqueryGenerator._actions_to_cdb(aquery_with_cc, "/exec", Path("/ws"))
        assert len(entries) == 1
        assert entries[0]["file"] == "src/mod.cc"
        # Sanity: when no recognisable suffix is present we drop, but we still
        # see the mnemonic accepted (i.e. no CppCompile substring assumption).
        assert BazelAqueryGenerator._actions_to_cdb(aquery_no_recognised_source, "/exec", Path("/ws")) == []

    @pytest.mark.parametrize("mnemonic", ["JavaCompile", "CppLink", "Action", "GoCompile"])
    def test_actions_to_cdb_drops_non_compile_actions(self, mnemonic: str) -> None:
        """Only mnemonics that end in ``Compile`` AND are C/C++-shaped land in
        the CDB. ``JavaCompile`` ends in ``Compile`` too and must be skipped
        when the action has no C/C++ source — current behaviour drops it via
        source-suffix filtering.
        """
        aquery = {
            "actions": [
                {"mnemonic": mnemonic, "arguments": ["javac", "-c", "src/x.java", "-o", "x.class"]},
            ]
        }
        assert BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws")) == []

    def test_actions_to_cdb_handles_null_actions_field(self) -> None:
        """Some proto-JSON encoders emit ``null`` for empty repeated fields;
        ``aquery.get("actions", [])`` would then return ``None`` and break the
        iteration. The ``or []`` defence must keep the parser silent.
        """
        assert BazelAqueryGenerator._actions_to_cdb({"actions": None}, "/exec", Path("/ws")) == []

    def test_actions_to_cdb_handles_null_mnemonics_field(self) -> None:
        """Regression guard for the sibling ``or []`` on the ``mnemonics``
        table — a null table must not stop a string-mnemonic action from
        landing in the CDB.
        """
        aquery = {
            "actions": [{"mnemonic": "CppCompile", "arguments": ["clang", "-c", "x.cc"]}],
            "mnemonics": None,
        }
        entries = BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws"))
        assert len(entries) == 1
        assert entries[0]["file"] == "x.cc"

    @pytest.mark.parametrize(
        "aquery",
        [
            {},
            {"actions": []},
            {"actions": None},
        ],
    )
    def test_actions_to_cdb_handles_edge_json_shapes(self, aquery: dict) -> None:
        assert BazelAqueryGenerator._actions_to_cdb(aquery, "/exec", Path("/ws")) == []


class TestBazelAqueryGeneratorFingerprint:
    """Bazel's ``cc_library`` typically uses ``glob()`` — adding a source
    file changes the build graph without editing any BUILD file, so the
    cached CDB must be invalidated.
    """

    def test_new_cpp_source_changes_fingerprint(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        (tmp_path / "BUILD.bazel").write_text('cc_library(name = "x", srcs = glob(["*.cc"]))\n')
        (tmp_path / "a.cc").write_text("int a() { return 0; }\n")

        from static_analyzer.cdb.fingerprint import compute_fingerprint

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

        write_cached_fingerprint(cdb_dir, _bazel_fingerprint([tmp_path / "MODULE.bazel"]))

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert runs == [], f"cache hit must not invoke bazel, got {runs}"

    def test_invalid_cache_regenerates(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text("[]")
        write_cached_fingerprint(cdb_dir, _bazel_fingerprint([tmp_path / "MODULE.bazel"]))

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return _make_fake_run()(argv, **kwargs)

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert any(argv[:2] == ["bazel", "aquery"] for argv in runs)

    def test_changing_bazel_query_scope_busts_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression for M9: flipping ``CODEBOARDING_CPP_BAZEL_QUERY`` from
        ``//src/...`` to ``//pkg/...`` must invalidate the cached CDB —
        otherwise the second run reuses entries scoped to the wrong package
        tree.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        # First run with one scope primes the cache.
        monkeypatch.setenv("CODEBOARDING_CPP_BAZEL_QUERY", "//src/...")
        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=_make_fake_run()),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        # Switch the scope; capture whether aquery re-runs.
        monkeypatch.setenv("CODEBOARDING_CPP_BAZEL_QUERY", "//pkg/...")
        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return _make_fake_run()(argv, **kwargs)

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert any(
            argv[:2] == ["bazel", "aquery"] for argv in runs
        ), "switching CODEBOARDING_CPP_BAZEL_QUERY should have busted the cache and re-run bazel aquery"

    def test_force_regenerate_ignores_valid_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        write_cached_fingerprint(cdb_dir, _bazel_fingerprint([tmp_path / "MODULE.bazel"]))
        monkeypatch.setenv("CODEBOARDING_CPP_FORCE_REGENERATE", "1")

        runs: list[list[str]] = []

        def recording_run(argv, **kwargs):
            runs.append(list(argv))
            return _make_fake_run()(argv, **kwargs)

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=recording_run),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        assert any(argv[:2] == ["bazel", "aquery"] for argv in runs)


class TestGeneratorForDispatch:
    """Plumbing test: BAZEL kind routes to the Bazel generator."""

    def test_bazel_kind_routes_to_bazel_generator(self) -> None:
        gen = generator_for(BuildSystemKind.BAZEL)
        assert isinstance(gen, BazelAqueryGenerator)

    @pytest.mark.parametrize("kind", [BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS])
    def test_windows_skips_bear_generator(self, kind: BuildSystemKind, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bear uses LD_PRELOAD and cannot run on Windows — the dispatcher
        must return ``None`` rather than yielding a generator that would
        fail with an opaque error later.
        """
        monkeypatch.setattr(sys, "platform", "win32")
        assert generator_for(kind) is None

    @pytest.mark.parametrize("kind", [BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS])
    def test_linux_routes_make_autotools_to_bear(self, kind: BuildSystemKind, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        gen = generator_for(kind)
        assert isinstance(gen, BearGenerator)
        assert isinstance(gen, CdbGenerator)

    def test_windows_still_routes_bazel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bazel itself works on Windows — only Bear is skipped."""
        monkeypatch.setattr(sys, "platform", "win32")
        assert isinstance(generator_for(BuildSystemKind.BAZEL), BazelAqueryGenerator)


class TestBazelAqueryQueryWrap:
    """Regression guard for the ``mnemonic("CppCompile", (<scope>))`` wrap.

    Why: the source comment at ``bazel_generator.py:97`` claims the wrap
    prevents a user-supplied ``CODEBOARDING_CPP_BAZEL_QUERY`` from closing
    the outer ``mnemonic(...)`` call and injecting sibling expressions.
    No prior test asserted the literal argv survives — a future refactor
    could silently strip the parentheses without anything failing.
    """

    @pytest.mark.parametrize(
        "hostile_scope",
        [
            # Closes the outer paren, unions a different scope.
            "//pkg) union //other",
            # Doubles the close to escape the wrap entirely.
            "//pkg)) union //other",
            # Bare close.
            ") // close early",
            # Try to nest a different mnemonic.
            '//pkg) union mnemonic("Run", //...) (//pkg',
            # Normal scope — control case.
            "//...",
            # Per-package scope.
            "//pkg/...",
        ],
    )
    def test_bazel_aquery_query_wrap_survives_injection_attempt(
        self,
        hostile_scope: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        monkeypatch.setenv("CODEBOARDING_CPP_BAZEL_QUERY", hostile_scope)

        captured_argv: list[list[str]] = []

        def recording_run(argv, **kwargs):
            captured_argv.append(list(argv))
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                return _fake_bazel_info_exec_root(tmp_path)
            if argv[:2] == ["bazel", "aquery"]:
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout=CANNED_AQUERY_JSON, stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bazel_generator.subprocess.run",
                side_effect=recording_run,
            ),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        aquery_argvs = [argv for argv in captured_argv if argv[:2] == ["bazel", "aquery"]]
        assert len(aquery_argvs) == 1, f"expected exactly one aquery call, got {aquery_argvs!r}"
        recorded_query = aquery_argvs[0][2]
        expected = f'mnemonic("CppCompile", ({hostile_scope}))'
        # Exact match — no extra whitespace, no missing parens, no escape.
        assert (
            recorded_query == expected
        ), f"query wrap leaked or changed: expected {expected!r}, got {recorded_query!r}"

    def test_default_query_is_also_wrapped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No env var -> default scope still goes through the same wrap."""
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
        monkeypatch.delenv("CODEBOARDING_CPP_BAZEL_QUERY", raising=False)

        captured_argv: list[list[str]] = []

        def recording_run(argv, **kwargs):
            captured_argv.append(list(argv))
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                return _fake_bazel_info_exec_root(tmp_path)
            if argv[:2] == ["bazel", "aquery"]:
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout=CANNED_AQUERY_JSON, stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bazel_generator.subprocess.run",
                side_effect=recording_run,
            ),
        ):
            BazelAqueryGenerator().generate(tmp_path)

        aquery_argvs = [argv for argv in captured_argv if argv[:2] == ["bazel", "aquery"]]
        assert aquery_argvs[0][2] == 'mnemonic("CppCompile", (deps(//...)))'


class TestRunBuildStepTimeoutHint:
    """M8 — Bazel routes through ``run_build_step``; the timeout branch must
    still suggest ``CODEBOARDING_CPP_BAZEL_QUERY`` alongside the shared
    ``CODEBOARDING_CPP_GENERATOR_TIMEOUT``.
    """

    def test_run_build_step_timeout_appends_hint(self, tmp_path: Path) -> None:
        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            timeout_value = kwargs.get("timeout", 60)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=float(timeout_value))  # type: ignore[arg-type]

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError) as excinfo:
                run_build_step(
                    ["bazel", "aquery", "//..."],
                    cwd=tmp_path,
                    step="bazel aquery",
                    timeout_hint="Or narrow CODEBOARDING_CPP_BAZEL_QUERY.",
                )

        message = str(excinfo.value)
        assert "CODEBOARDING_CPP_GENERATOR_TIMEOUT" in message
        assert "Or narrow CODEBOARDING_CPP_BAZEL_QUERY." in message

    def test_run_build_step_timeout_without_hint_is_unchanged(self, tmp_path: Path) -> None:
        """Other generators (Bear, CMake, Meson, Ninja) don't pass a hint —
        their error message must remain a clean pointer at ``ENV_TIMEOUT``
        without trailing whitespace.
        """

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            timeout_value = kwargs.get("timeout", 60)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=float(timeout_value))  # type: ignore[arg-type]

        with patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError) as excinfo:
                run_build_step(["make"], cwd=tmp_path, step="bear make")

        message = str(excinfo.value)
        assert "CODEBOARDING_CPP_GENERATOR_TIMEOUT" in message
        assert "CODEBOARDING_CPP_BAZEL_QUERY" not in message
        assert message == message.rstrip()

    def test_bazel_aquery_timeout_surfaces_bazel_query_hint(self, tmp_path: Path) -> None:
        """End-to-end: the Bazel generator's timeout error mentions both the
        shared timeout knob and the Bazel-specific scope knob.
        """
        (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")

        def fake_run(argv, **kwargs):
            if argv[:2] == ["bazel", "--version"]:
                return _fake_bazel_version_output()
            if argv[:2] == ["bazel", "info"]:
                return _fake_bazel_info_exec_root(tmp_path)
            if argv[:2] == ["bazel", "aquery"]:
                timeout_value = kwargs.get("timeout", 60)
                raise subprocess.TimeoutExpired(cmd=argv, timeout=float(timeout_value))
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch("static_analyzer.cdb.base.subprocess.run", side_effect=fake_run),
            patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                BazelAqueryGenerator().generate(tmp_path)

        message = str(excinfo.value)
        assert "bazel aquery timed out" in message
        assert "CODEBOARDING_CPP_GENERATOR_TIMEOUT" in message
        assert "CODEBOARDING_CPP_BAZEL_QUERY" in message


class TestBazelInfoTimeout:
    """``_bazel_info`` used to hardcode ``timeout=60`` — slow cold workspaces
    timed out before the user-set ``CODEBOARDING_CPP_GENERATOR_TIMEOUT`` had
    any effect, and the error message named neither the timeout nor the env
    var to bump.
    """

    def test_bazel_info_honors_generator_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATOR_TIMEOUT", "120")
        captured: list[dict] = []

        def recording_run(argv, **kwargs):
            captured.append(dict(kwargs))
            return _fake_bazel_info_exec_root(tmp_path)

        with patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=recording_run):
            BazelAqueryGenerator._bazel_info(tmp_path, "execution_root")

        assert len(captured) == 1
        assert captured[0].get("timeout") == 120

    def test_bazel_info_timeout_message_mentions_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATOR_TIMEOUT", "120")

        def fake_run(argv, **kwargs):
            timeout_value = kwargs.get("timeout", 60)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=float(timeout_value))

        with patch("static_analyzer.cdb.bazel_generator.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError) as excinfo:
                BazelAqueryGenerator._bazel_info(tmp_path, "execution_root")

        message = str(excinfo.value)
        assert config.ENV_TIMEOUT in message
        assert "120" in message
