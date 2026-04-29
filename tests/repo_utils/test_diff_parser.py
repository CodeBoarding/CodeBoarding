from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_utils.diff_parser import detect_changes


def _completed(stdout: str) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_worktree_diff_includes_untracked_files():
    diff_out = ":100644 100644 aaaa bbbb M\tsrc/existing.py\n"
    untracked_out = "src/new_helper.py\0"

    with patch(
        "repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out), _completed(untracked_out)]
    ) as run:
        parsed = detect_changes(Path("/tmp/repo"), "HEAD", target_ref="")

    assert run.call_count == 2
    diff_cmd = run.call_args_list[0].args[0]
    assert diff_cmd[:3] == ["git", "diff", "--raw"]
    ls_cmd = run.call_args_list[1].args[0]
    assert ls_cmd[:5] == ["git", "ls-files", "--others", "--exclude-standard", "-z"]

    paths = [f.file_path for f in parsed.files]
    assert paths == ["src/existing.py", "src/new_helper.py"]

    new_entry = parsed.get_file("src/new_helper.py")
    assert new_entry is not None
    assert new_entry.status_code == "A"
    assert new_entry.hunks == []


def test_non_source_extensions_are_filtered():
    """Tracked and untracked changes to files with unsupported extensions are dropped.

    Why: parsed_diff is the boundary that defines CodeBoarding's change set;
    filtering here keeps non-source edits (docs, configs) from flowing through
    the incremental pipeline and producing empty work.
    """
    diff_out = (
        ":100644 100644 aaaa bbbb M\tsrc/keep.py\n"
        ":100644 100644 cccc dddd M\tREADME.md\n"
        ":100644 100644 eeee ffff M\tdocs/guide.rst\n"
    )
    untracked_out = "src/new.py\0notes.md\0config.yaml\0"

    with patch("repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out), _completed(untracked_out)]):
        parsed = detect_changes(Path("/tmp/repo"), "HEAD", target_ref="")

    paths = [f.file_path for f in parsed.files]
    assert paths == ["src/keep.py", "src/new.py"]


def test_rename_keeps_entry_when_either_side_is_source():
    """Renames are retained if at least one of old/new paths is a source file."""
    diff_out = ":100644 100644 aaaa bbbb R100\tsrc/old.py\tsrc/new.py\n"

    with patch("repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out), _completed("")]):
        parsed = detect_changes(Path("/tmp/repo"), "HEAD", target_ref="")

    assert [f.file_path for f in parsed.files] == ["src/new.py"]
    assert parsed.files[0].old_path == "src/old.py"


def test_untracked_files_skipped_when_comparing_commits():
    diff_out = ":100644 100644 aaaa bbbb M\tfile.py\n"
    with patch("repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out)]) as run:
        parsed = detect_changes(Path("/tmp/repo"), "v1.0", target_ref="v1.1")

    assert run.call_count == 1
    assert [f.file_path for f in parsed.files] == ["file.py"]


def test_untracked_enumeration_failure_does_not_erase_diff():
    from subprocess import CalledProcessError

    diff_out = ":100644 100644 aaaa bbbb M\tfile.py\n"
    ls_err = CalledProcessError(128, ["git", "ls-files"], output="", stderr="fatal: not a git repository")

    with patch("repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out), ls_err]):
        parsed = detect_changes(Path("/tmp/repo"), "HEAD", target_ref="")

    assert [f.file_path for f in parsed.files] == ["file.py"]
    assert parsed.error is None


def test_untracked_duplicate_is_deduplicated():
    diff_out = ":100644 100644 aaaa bbbb A\tsrc/new_helper.py\n"
    untracked_out = "src/new_helper.py\0"

    with patch("repo_utils.git_ops.subprocess.run", side_effect=[_completed(diff_out), _completed(untracked_out)]):
        parsed = detect_changes(Path("/tmp/repo"), "HEAD", target_ref="")

    assert [f.file_path for f in parsed.files] == ["src/new_helper.py"]
