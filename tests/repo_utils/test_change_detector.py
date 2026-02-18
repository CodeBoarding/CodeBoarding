from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_utils.change_detector import ChangeType, detect_changes_from_commit, detect_uncommitted_changes


def test_detect_uncommitted_changes_uses_working_tree_diff():
    with patch(
        "repo_utils.change_detector.subprocess.run",
        return_value=CompletedProcess(
            args=["git", "diff"],
            returncode=0,
            stdout="M\tpyproject.toml\n",
            stderr="",
        ),
    ) as run_mock:
        changes = detect_uncommitted_changes(Path("/tmp/repo"))

    run_mock.assert_called_once()
    command = run_mock.call_args.args[0]
    assert command == [
        "git",
        "diff",
        "--name-status",
        "-M",
        "-C",
        "--find-renames=50%",
        "HEAD",
    ]
    assert len(changes.changes) == 1
    assert changes.changes[0].change_type == ChangeType.MODIFIED
    assert changes.changes[0].file_path == "pyproject.toml"


def test_detect_changes_from_commit_uses_working_tree_target():
    with patch(
        "repo_utils.change_detector.subprocess.run",
        return_value=CompletedProcess(
            args=["git", "diff"],
            returncode=0,
            stdout="A\trequirements/base.txt\n",
            stderr="",
        ),
    ) as run_mock:
        changes = detect_changes_from_commit(Path("/tmp/repo"), "abc123")

    run_mock.assert_called_once()
    command = run_mock.call_args.args[0]
    assert command == [
        "git",
        "diff",
        "--name-status",
        "-M",
        "-C",
        "--find-renames=50%",
        "abc123",
    ]
    assert len(changes.changes) == 1
    assert changes.changes[0].change_type == ChangeType.ADDED
    assert changes.changes[0].file_path == "requirements/base.txt"
