"""Cross-platform: git output must decode, and name non-ASCII paths verbatim.

Default ``core.quotepath=true`` makes git C-quote bytes >= 0x80 (e.g. an
accented ``e`` becomes the literal string ``"\\303\\251"``), and the quoted
form never matches the live worktree. ``git_ops._git_argv`` prepends
``-c core.quotepath=false`` so paths arrive as raw UTF-8 bytes; these tests
protect against a refactor reverting it.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from infra.repo_utils.git_ops import get_current_commit


def _completed(stdout: str = "") -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_git_argv_disables_quotepath():
    with patch(
        "infra.repo_utils.git_ops.subprocess.run",
        return_value=_completed("abc123\n"),
    ) as run:
        get_current_commit(Path("/tmp/repo"))
    for call in run.call_args_list:
        argv = call.args[0]
        assert "-c" in argv and "core.quotepath=false" in argv


def test_text_decoding_uses_utf8_replace():
    """Even on a non-UTF-8 locale, the helper kwargs decode UTF-8 bytes safely.

    Why: bare ``text=True`` decodes via ``locale.getpreferredencoding()`` and
    raises ``UnicodeDecodeError`` on Windows cp1252 when git emits UTF-8.
    """
    with patch(
        "infra.repo_utils.git_ops.subprocess.run",
        return_value=_completed("abc123\n"),
    ) as run:
        get_current_commit(Path("/tmp/repo"))
    kwargs = run.call_args_list[0].kwargs
    assert kwargs.get("encoding") == "utf-8"
    assert kwargs.get("errors") == "replace"
    assert kwargs.get("text") is True
