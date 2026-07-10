"""Cross-platform: non-ASCII filenames must round-trip through git output.

Default ``core.quotepath=true`` makes git C-quote bytes >= 0x80 (e.g. an
accented ``e`` becomes the literal string ``"\\303\\251"``), and the quoted
form never matches the live worktree. The fix in ``git_ops._git_argv``
prepends ``-c core.quotepath=false`` so paths arrive as raw UTF-8 bytes
that ``Path.exists()`` can resolve.

Source intentionally avoids literal non-ASCII glyphs (the repo enforces
cp1252-encodable .py files for Windows logging compatibility -- see
``tests/test_windows_encoding.py``); test data uses ``\\u`` escapes.

Two layers of test:

* Unit (mock): assert the argv carries the config flag -- protects against
  a future refactor reverting the helper without anyone noticing.
* Integration (real git): create a real repo with a non-ASCII tracked
  file, modify it, and confirm the changed-paths set contains the on-disk
  path verbatim. Skipped if git isn't on PATH or the local FS rejects the
  filenames (unusual but possible on case-insensitive Windows configs).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from repo_utils.git_ops import (
    _list_uncommitted_changed_files,
    get_changed_files_since,
)


# ---------------------------------------------------------------------------
# Unit: argv carries ``-c core.quotepath=false``
# ---------------------------------------------------------------------------


def _completed(stdout: str = "") -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _argvs(run_mock) -> list[list[str]]:
    return [call.args[0] for call in run_mock.call_args_list]


def test_get_changed_files_since_argv_disables_quotepath():
    # 4 calls: outer diff + 3 inner uncommitted-list calls.
    with patch(
        "repo_utils.git_ops.subprocess.run",
        side_effect=[_completed("") for _ in range(4)],
    ) as run:
        get_changed_files_since(Path("/tmp/repo"), "HEAD")
    for argv in _argvs(run):
        assert "-c" in argv and "core.quotepath=false" in argv


def test_text_decoding_uses_utf8_replace():
    """Even on a non-UTF-8 locale, the helper kwargs decode UTF-8 bytes safely.

    Why: bare ``text=True`` decodes via ``locale.getpreferredencoding()`` and
    raises ``UnicodeDecodeError`` on Windows cp1252 when git emits UTF-8.
    """
    with patch(
        "repo_utils.git_ops.subprocess.run",
        side_effect=[_completed("") for _ in range(4)],
    ) as run:
        get_changed_files_since(Path("/tmp/repo"), "HEAD")
    kwargs = run.call_args_list[0].kwargs
    assert kwargs.get("encoding") == "utf-8"
    assert kwargs.get("errors") == "replace"
    assert kwargs.get("text") is True


# ---------------------------------------------------------------------------
# Integration: real git repo with non-ASCII filenames
# ---------------------------------------------------------------------------


# Latin-1 small letter e with acute (U+00E9) and CJK "Japanese" (U+65E5 U+672C U+8A9E).
# Escape sequences keep the .py source pure-ASCII for cp1252 logging compat.
_NON_ASCII_NAMES = (chr(0x00E9) + ".py", chr(0x65E5) + chr(0x672C) + chr(0x8A9E) + ".py")


def _git_available() -> bool:
    return shutil.which("git") is not None


def _can_create_non_ascii_files(directory: Path) -> bool:
    """Probe whether the host filesystem accepts the non-ASCII names we use."""
    try:
        for name in _NON_ASCII_NAMES:
            (directory / name).touch()
            (directory / name).unlink()
        return True
    except (OSError, UnicodeError):
        return False


@pytest.fixture
def real_repo(tmp_path: Path):
    if not _git_available():
        pytest.skip("git not on PATH")
    if not _can_create_non_ascii_files(tmp_path):
        pytest.skip("filesystem rejects non-ASCII filenames in this environment")

    def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        # Force a deterministic identity so ``git commit`` doesn't fail in CI
        # environments without ~/.gitconfig.
        env_args = (
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
        )
        return subprocess.run(
            ["git", *env_args, *args],
            cwd=tmp_path,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    _git("init", "-b", "main")
    for name in _NON_ASCII_NAMES:
        (tmp_path / name).write_text("# initial\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-m", "init")
    return tmp_path, _git


def test_get_changed_files_since_returns_real_paths(real_repo):
    repo, git = real_repo
    base = git("rev-parse", "HEAD").stdout.strip()

    # Modify both non-ASCII files and commit again.
    for name in _NON_ASCII_NAMES:
        (repo / name).write_text("# modified\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "edit")

    changed = get_changed_files_since(repo, base)
    # All returned paths must point at real files on disk -- pre-fix, C-quoted
    # paths satisfied neither.
    for path in changed:
        assert path.exists(), f"{path!r} from get_changed_files_since does not exist"

    names = {p.name for p in changed}
    for expected in _NON_ASCII_NAMES:
        assert expected in names, f"missing {expected!r} in {names!r}"


def test_get_changed_files_since_includes_uncommitted_delete(real_repo):
    repo, git = real_repo
    base = git("rev-parse", "HEAD").stdout.strip()
    deleted = _NON_ASCII_NAMES[0]

    (repo / deleted).unlink()

    changed = get_changed_files_since(repo, base)
    assert repo / deleted in changed


def test_get_changed_files_since_includes_staged_delete(real_repo):
    repo, git = real_repo
    base = git("rev-parse", "HEAD").stdout.strip()
    deleted = _NON_ASCII_NAMES[0]

    (repo / deleted).unlink()
    git("add", "-A")

    changed = get_changed_files_since(repo, base)
    assert repo / deleted in changed


def test_get_changed_files_since_includes_both_sides_of_rename(real_repo):
    repo, git = real_repo
    base = git("rev-parse", "HEAD").stdout.strip()
    old_name = _NON_ASCII_NAMES[0]
    new_name = "renamed_" + old_name

    (repo / old_name).rename(repo / new_name)
    git("add", "-A")
    git("commit", "-m", "rename")

    changed = get_changed_files_since(repo, base)
    assert repo / old_name in changed
    assert repo / new_name in changed


def test_uncommitted_changed_files_include_non_ascii(real_repo):
    repo, _ = real_repo
    target = _NON_ASCII_NAMES[1]
    (repo / target).write_text("# uncommitted edit\n", encoding="utf-8")

    paths = _list_uncommitted_changed_files(repo)
    names = {p.name for p in paths}
    assert target in names


# ---------------------------------------------------------------------------
# Skip the integration block on Windows when the console code page is mock-y
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    # Real-git tests still run on Windows CI -- the helper turns off C-quoting,
    # and Python's UTF-8 mode (PEP 686, default on 3.15+) handles the decode.
    # Older Pythons may need ``PYTHONUTF8=1``; emit a hint rather than skip.
    pass
