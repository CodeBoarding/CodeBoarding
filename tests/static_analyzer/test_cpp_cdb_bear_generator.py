"""Unit tests for BearGenerator — subprocess is mocked.

Integration tests that actually invoke Bear live under
``tests/integration/`` and are gated on ``shutil.which('bear')``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind
from static_analyzer.engine.adapters.cpp_cdb.bear_generator import BearGenerator
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import write_cached_fingerprint, compute_fingerprint


VALID_CDB_JSON = '[{"directory": ".", "file": "x.c", "command": "cc -c x.c"}]'


def _fake_bear_version() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["bear", "--version"], returncode=0, stdout="bear 3.1.3\n", stderr="")


def _fake_success(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    """Default ``subprocess.run`` stand-in: report success for every call."""
    if argv[:2] == ["bear", "--version"]:
        return _fake_bear_version()
    return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")


class TestBearGeneratorInit:
    def test_rejects_unsupported_kind(self) -> None:
        with pytest.raises(ValueError, match=r"cannot handle"):
            BearGenerator(BuildSystemKind.BAZEL)


class TestBearGeneratorPreflight:
    """Missing tools and old Bear versions must produce actionable errors."""

    def test_missing_bear_raises(self, tmp_path: Path) -> None:
        real_which = __import__("shutil").which

        def selective(name: str) -> str | None:
            return None if name == "bear" else real_which(name)

        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
        with patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"bear.*PATH"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

    def test_bear_too_old_raises(self, tmp_path: Path) -> None:
        """Bear 2.x's CLI is incompatible with the 3.x invocation; fail loudly."""
        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            assert argv[:2] == ["bear", "--version"]
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="bear 2.4.2\n", stderr="")

        with (
            patch(
                "static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which",
                side_effect=lambda n: "/usr/bin/" + n,
            ),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"Bear 2"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

    def test_missing_make_raises(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")

        def selective(name: str) -> str | None:
            return None if name == "make" else f"/usr/bin/{name}"

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=selective),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=_fake_success),
        ):
            with pytest.raises(RuntimeError, match=r"'make'"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)


class TestBearGeneratorMake:
    """Happy-path and failure-mode tests for Make builds."""

    def _all_present(self, name: str) -> str:
        return f"/usr/bin/{name}"

    def test_successful_run_writes_cdb(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            # bear --output <path> -- make ...: write the CDB as a real bear would
            if argv[0] == "bear":
                out_idx = argv.index("--output") + 1
                Path(argv[out_idx]).write_text('[{"file": "x.c", "command": "cc", "directory": "."}]')
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            cdb = BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

        assert cdb.is_file()
        assert cdb == tmp_path / ".codeboarding" / "cdb" / "compile_commands.json"
        # Fingerprint marker written alongside the CDB
        assert (cdb.parent / ".fingerprint").is_file()

    def test_fingerprint_cache_skips_rebuild(self, tmp_path: Path) -> None:
        """Second invocation with identical inputs must not invoke subprocess.run
        for make/bear — only the version probe is allowed."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("all:\n\ttrue\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        # Prime the cache
        write_cached_fingerprint(cdb_dir, compute_fingerprint([makefile]))

        runs: list[list[str]] = []

        def recording_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            runs.append(argv)
            return _fake_success(argv, **kwargs)

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=recording_run),
        ):
            out = BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

        assert out == cdb_path
        # Cache hit short-circuits before we even probe bear.
        assert runs == [], f"expected no subprocess calls on cache hit, got {runs}"

    def test_new_cpp_source_invalidates_cache(self, tmp_path: Path) -> None:
        """Adding ``src/new.cc`` must bust the cached CDB even when the
        Makefile hasn't changed — a new file is a new compile command, and
        Bear only records commands it actually intercepts.
        """
        makefile = tmp_path / "Makefile"
        makefile.write_text("all:\n\ttrue\n")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "greeter.cpp").write_text("void greet() {}\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        generator = BearGenerator(BuildSystemKind.MAKE)
        write_cached_fingerprint(cdb_dir, compute_fingerprint(generator._fingerprint_inputs(tmp_path)))

        # A new source appears — must bust the cache on the next call.
        (src_dir / "new.cc").write_text("int new_fn() { return 0; }\n")

        runs: list[list[str]] = []

        def recording_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            runs.append(list(argv))
            if argv[0] == "bear" and "--output" in argv:
                Path(argv[argv.index("--output") + 1]).write_text(VALID_CDB_JSON)
            return _fake_success(argv, **kwargs)

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=recording_run),
        ):
            generator.generate(tmp_path)

        assert any(
            r[:1] == ["bear"] and "--output" in r for r in runs
        ), "new source file should have busted the cache and forced a bear rerun"

    def test_fingerprint_invalidated_on_makefile_change(self, tmp_path: Path) -> None:
        """Edit the Makefile and the old CDB is considered stale, then rebuild."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("all:\n\ttrue\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        write_cached_fingerprint(cdb_dir, compute_fingerprint([makefile]))
        makefile.write_text("all:\n\techo different\n")

        runs: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            runs.append(argv)
            if argv[0] == "bear" and "--output" in argv:
                Path(argv[argv.index("--output") + 1]).write_text(VALID_CDB_JSON)
            return _fake_success(argv, **kwargs)

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

        assert any(r[:1] == ["bear"] and "--output" in r for r in runs), "stale cache should force a rebuild"

    def test_make_failure_surfaces_stderr(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\tfalse\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            return subprocess.CompletedProcess(
                args=argv,
                returncode=2,
                stdout="",
                stderr="make[1]: *** [all] Error 1\n",
            )

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"Error 1"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

    def test_timeout_surfaces_env_hint(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            timeout_value = kwargs.get("timeout", 60)
            raise subprocess.TimeoutExpired(cmd=argv, timeout=float(timeout_value))  # type: ignore[arg-type]

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"CODEBOARDING_CPP_GENERATOR_TIMEOUT"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

    def test_empty_output_raises(self, tmp_path: Path) -> None:
        """Bear succeeded but captured no commands — treat as failure so the
        user sees a message instead of clangd silently producing zero refs.
        """
        (tmp_path / "Makefile").write_text("all:\n\t@echo up-to-date\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"produced no compile_commands"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

    def test_invalid_output_deletes_stale_generated_cache(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text("all:\n\ttrue\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_path.write_text(VALID_CDB_JSON)
        write_cached_fingerprint(cdb_dir, "stale")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            if argv[0] == "bear" and "--output" in argv:
                Path(argv[argv.index("--output") + 1]).write_text("[]")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(RuntimeError, match=r"invalid compile_commands"):
                BearGenerator(BuildSystemKind.MAKE).generate(tmp_path)

        assert not cdb_path.exists()
        assert not (cdb_dir / ".fingerprint").exists()


class TestBearGeneratorAutotools:
    """Autotools goes through 2–3 steps before bear runs."""

    def _all_present(self, name: str) -> str:
        return f"/usr/bin/{name}"

    def test_runs_configure_then_bear_when_configure_present(self, tmp_path: Path) -> None:
        """Pre-existing ./configure: skip autoreconf, run configure out-of-tree, then bear."""
        (tmp_path / "configure.ac").write_text("AC_INIT([x], [0.1])\n")
        configure = tmp_path / "configure"
        configure.write_text("#!/bin/sh\nexit 0\n")
        configure.chmod(0o755)

        recorded: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            recorded.append(list(argv))
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            if argv[0] == "bear" and "--output" in argv:
                Path(argv[argv.index("--output") + 1]).write_text(VALID_CDB_JSON)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=fake_run),
        ):
            BearGenerator(BuildSystemKind.AUTOTOOLS).generate(tmp_path)

        # No autoreconf because ./configure was already present
        assert not any(argv[:1] == ["autoreconf"] for argv in recorded)
        # ./configure ran from the scratch build dir, not the source dir
        configure_calls = [argv for argv in recorded if argv[0].endswith("/configure")]
        assert configure_calls, "configure must be executed"
        # Bear runs make in the scratch build dir
        bear_calls = [argv for argv in recorded if argv[0] == "bear" and "--output" in argv]
        assert bear_calls

    def test_runs_autoreconf_when_configure_missing(self, tmp_path: Path) -> None:
        """Pristine git checkout: ./configure doesn't exist, autoreconf must run first."""
        (tmp_path / "configure.ac").write_text("AC_INIT([x], [0.1])\n")

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if argv[:2] == ["bear", "--version"]:
                return _fake_bear_version()
            if argv[0] == "autoreconf":
                # Simulate autoreconf generating the script
                (tmp_path / "configure").write_text("#!/bin/sh\nexit 0\n")
                (tmp_path / "configure").chmod(0o755)
            if argv[0] == "bear" and "--output" in argv:
                Path(argv[argv.index("--output") + 1]).write_text(VALID_CDB_JSON)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        recorded: list[list[str]] = []

        def tracking_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            recorded.append(list(argv))
            return fake_run(argv, **kwargs)

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=self._all_present),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=tracking_run),
        ):
            BearGenerator(BuildSystemKind.AUTOTOOLS).generate(tmp_path)

        assert any(argv[:1] == ["autoreconf"] for argv in recorded), "autoreconf should have been invoked"

    def test_missing_autoreconf_raises_when_configure_absent(self, tmp_path: Path) -> None:
        (tmp_path / "configure.ac").write_text("AC_INIT([x], [0.1])\n")

        def selective(name: str) -> str | None:
            return None if name == "autoreconf" else f"/usr/bin/{name}"

        with (
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.shutil.which", side_effect=selective),
            patch("static_analyzer.engine.adapters.cpp_cdb.bear_generator.subprocess.run", side_effect=_fake_success),
        ):
            with pytest.raises(RuntimeError, match=r"autoreconf"):
                BearGenerator(BuildSystemKind.AUTOTOOLS).generate(tmp_path)
