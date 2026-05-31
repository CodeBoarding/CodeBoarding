"""Regression tests for HIGH#6: non-UTF-8 build output must not crash.

Every subprocess in the CDB layer runs with ``text=True``. Without
``errors="replace"``, a localized toolchain emitting non-UTF-8 bytes
(German ``make`` on CP1252, French ``bazel``, etc.) crashes
``subprocess.run`` itself with ``UnicodeDecodeError`` — outside every
``except`` block — and the whole LSP startup goes down.

These tests exercise each call site with a real tiny subprocess that
emits a 0xFF byte (invalid UTF-8) on stdout or stderr, and assert the
helpers complete or raise a clean ``RuntimeError`` instead of
``UnicodeDecodeError``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.cdb.base import run_build_step
from static_analyzer.cdb.bazel_generator import BazelAqueryGenerator
from static_analyzer.cdb.bear_generator import BearGenerator


_EMIT_NON_UTF8_STDOUT = "import sys; sys.stdout.buffer.write(b'hello\\xff\\n'); sys.stdout.flush()"
_EMIT_NON_UTF8_BOTH_FAIL = "import sys; sys.stderr.buffer.write(b'boom\\xff\\n'); sys.stderr.flush(); sys.exit(2)"


class TestRunBuildStepDecoding:
    """``run_build_step`` is the shared subprocess helper in ``base.py``."""

    def test_run_build_step_handles_non_utf8_stdout(self, tmp_path: Path) -> None:
        result = run_build_step(
            [sys.executable, "-c", _EMIT_NON_UTF8_STDOUT],
            cwd=tmp_path,
            step="probe",
        )
        # The 0xFF byte must have decoded as U+FFFD (replacement char), not raised.
        assert "hello" in result.stdout
        assert "\ufffd" in result.stdout

    def test_run_build_step_handles_non_utf8_stderr_on_failure(self, tmp_path: Path) -> None:
        # Non-zero exit so run_build_step formats the stderr tail; that tail
        # must not crash decoding either.
        with pytest.raises(RuntimeError) as excinfo:
            run_build_step(
                [sys.executable, "-c", _EMIT_NON_UTF8_BOTH_FAIL],
                cwd=tmp_path,
                step="probe",
            )
        # Failure surfaces a stderr tail — replaced char proves we decoded.
        assert "boom" in str(excinfo.value)
        assert not isinstance(excinfo.value.__cause__, UnicodeDecodeError)


class TestBearSubprocessRunDecoding:
    """Bear's private duplicate of ``run_build_step`` (``_subprocess_run``)."""

    def test_subprocess_run_handles_non_utf8_stdout(self, tmp_path: Path) -> None:
        # Success path: no exception of any kind.
        BearGenerator._subprocess_run(
            [sys.executable, "-c", _EMIT_NON_UTF8_STDOUT],
            cwd=tmp_path,
            step="probe",
        )

    def test_subprocess_run_handles_non_utf8_stderr_on_failure(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError) as excinfo:
            BearGenerator._subprocess_run(
                [sys.executable, "-c", _EMIT_NON_UTF8_BOTH_FAIL],
                cwd=tmp_path,
                step="probe",
            )
        assert "boom" in str(excinfo.value)
        assert not isinstance(excinfo.value.__cause__, UnicodeDecodeError)


class TestBearVersionProbeDecoding:
    """``_require_bear`` shells out to ``bear --version`` directly."""

    def test_require_bear_handles_non_utf8_stderr(self) -> None:
        """A localized Bear printing non-UTF-8 bytes must not crash the probe.

        Why: the version probe sits before any user-facing error handling,
        so a UnicodeDecodeError here takes the whole LSP startup down.
        """
        fake = subprocess.CompletedProcess(
            args=["bear", "--version"],
            returncode=1,
            stdout="",
            # Simulates what ``subprocess.run(..., text=True, errors="replace")``
            # would have produced from raw bytes b"oops\xff\n".
            stderr="oops\ufffd\n",
        )

        with (
            patch(
                "static_analyzer.cdb.bear_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bear_generator.subprocess.run",
                return_value=fake,
            ),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                BearGenerator._require_bear()

        assert "bear --version" in str(excinfo.value)
        assert not isinstance(excinfo.value.__cause__, UnicodeDecodeError)


class TestBazelVersionProbeDecoding:
    """``_require_bazel`` shells out to ``bazel --version`` directly."""

    def test_require_bazel_handles_non_utf8_stderr(self) -> None:
        fake = subprocess.CompletedProcess(
            args=["bazel", "--version"],
            returncode=1,
            stdout="",
            stderr="boom\ufffd\n",
        )

        with (
            patch(
                "static_analyzer.cdb.bazel_generator.shutil.which",
                side_effect=lambda n: f"/usr/bin/{n}",
            ),
            patch(
                "static_analyzer.cdb.bazel_generator.subprocess.run",
                return_value=fake,
            ),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                BazelAqueryGenerator._require_bazel()

        assert "bazel --version" in str(excinfo.value)
        assert not isinstance(excinfo.value.__cause__, UnicodeDecodeError)


class TestBazelInfoDecoding:
    """``_bazel_info`` shells out to ``bazel info <key>`` directly."""

    def test_bazel_info_handles_non_utf8_stderr_on_failure(self, tmp_path: Path) -> None:
        fake = subprocess.CompletedProcess(
            args=["bazel", "info", "execution_root"],
            returncode=1,
            stdout="",
            stderr="bad\ufffd\n",
        )

        with patch(
            "static_analyzer.cdb.bazel_generator.subprocess.run",
            return_value=fake,
        ):
            with pytest.raises(RuntimeError) as excinfo:
                BazelAqueryGenerator._bazel_info(tmp_path, "execution_root")

        assert "bazel info execution_root" in str(excinfo.value)
        assert not isinstance(excinfo.value.__cause__, UnicodeDecodeError)


class TestSubprocessCallSitesPassErrorsReplace:
    """Pin the contract: every ``subprocess.run`` invocation in the CDB layer
    sets ``errors="replace"`` so any future call site doesn't silently
    regress to the strict default.
    """

    @pytest.mark.parametrize(
        "module_name",
        [
            "static_analyzer.cdb.base",
            "static_analyzer.cdb.bear_generator",
            "static_analyzer.cdb.bazel_generator",
        ],
    )
    def test_subprocess_run_call_sites_use_errors_replace(self, module_name: str) -> None:
        import importlib
        import inspect

        module = importlib.import_module(module_name)
        source = inspect.getsource(module)
        # For every text=True occurrence in the module, errors="replace" must
        # appear within ~10 lines after it.
        lines = source.splitlines()
        text_true_lines = [i for i, line in enumerate(lines) if "text=True" in line]
        assert text_true_lines, f"{module_name}: expected at least one text=True call"
        for idx in text_true_lines:
            window = "\n".join(lines[max(0, idx - 10) : idx + 10])
            assert 'errors="replace"' in window, (
                f"{module_name}:{idx + 1}: subprocess.run with text=True is missing "
                f'errors="replace" — strict decoder will crash on non-UTF-8 bytes'
            )
