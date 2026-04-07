from pathlib import Path
from unittest.mock import patch

from repo_utils.change_detector import ChangeType, detect_changes_from_commit, detect_uncommitted_changes
from repo_utils.parsed_diff import ParsedDiffFile, ParsedGitDiff


def test_detect_uncommitted_changes_uses_working_tree_diff():
    with patch(
        "repo_utils.change_detector.load_parsed_git_diff",
        return_value=ParsedGitDiff(
            base_ref="HEAD",
            target_ref="",
            files=[ParsedDiffFile(status_code="M", file_path="pyproject.toml")],
        ),
    ) as load_mock:
        changes = detect_uncommitted_changes(Path("/tmp/repo"))

    load_mock.assert_called_once_with(Path("/tmp/repo"), "HEAD", "", fetch_missing_refs=True)
    assert len(changes.changes) == 1
    assert changes.changes[0].change_type == ChangeType.MODIFIED
    assert changes.changes[0].file_path == "pyproject.toml"
    assert changes.parsed_diff is not None


def test_detect_changes_from_commit_uses_working_tree_target():
    with patch(
        "repo_utils.change_detector.load_parsed_git_diff",
        return_value=ParsedGitDiff(
            base_ref="abc123",
            target_ref="",
            files=[ParsedDiffFile(status_code="A", file_path="requirements/base.txt")],
        ),
    ) as load_mock:
        changes = detect_changes_from_commit(Path("/tmp/repo"), "abc123")

    load_mock.assert_called_once_with(Path("/tmp/repo"), "abc123", "", fetch_missing_refs=True)
    assert len(changes.changes) == 1
    assert changes.changes[0].change_type == ChangeType.ADDED
    assert changes.changes[0].file_path == "requirements/base.txt"
