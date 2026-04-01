"""Tests for repo_utils.method_diff – method-level diff status classification."""

import pytest
from unittest.mock import patch

from agents.agent_responses import MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange
from repo_utils.method_diff import get_method_statuses_for_file

from pathlib import Path


def _make_changeset(*, modified: list[str] | None = None, base_ref: str = "HEAD~1") -> ChangeSet:
    changes: list[DetectedChange] = []
    for f in modified or []:
        changes.append(DetectedChange(change_type=ChangeType.MODIFIED, file_path=f))
    return ChangeSet(changes=changes, base_ref=base_ref)


class TestNewFunctionInMixedHunk:
    """Reproduce: a hunk that modifies a few existing lines AND adds many new
    lines should mark a brand-new function (fully inside the added portion) as
    ADDED, not MODIFIED.

    Real-world example: ``static_analyzer/cluster_helpers.py`` had a hunk
    ``@@ -42,10 +69,367 @@`` which modified 10 old lines into 367 new lines.
    ``merge_clusters`` (lines 385-424) is entirely new code, but the current
    logic puts the whole 69-435 range into ``changed_ranges`` (because
    ``old_count=10 > 0``), so the function is classified as MODIFIED.
    """

    @pytest.fixture
    def file_path(self) -> str:
        return "static_analyzer/cluster_helpers.py"

    @pytest.fixture
    def changes(self, file_path: str) -> ChangeSet:
        return _make_changeset(modified=[file_path])

    @pytest.fixture
    def hunks(self) -> list[tuple[int, int, int, int]]:
        # Mimics @@ -42,10 +69,367 @@
        return [(42, 10, 69, 367)]

    @pytest.fixture
    def methods(self) -> list[MethodEntry]:
        return [
            # Existing function that was modified (overlaps with the old 10 lines)
            MethodEntry(
                qualified_name="cluster_helpers.build_all_cluster_results",
                start_line=69,
                end_line=110,
                node_type="FUNCTION",
            ),
            # Brand-new function, fully inside the added portion of the hunk
            MethodEntry(
                qualified_name="cluster_helpers.merge_clusters",
                start_line=385,
                end_line=424,
                node_type="FUNCTION",
            ),
            # Unchanged function outside the hunk
            MethodEntry(
                qualified_name="cluster_helpers.get_all_cluster_ids",
                start_line=440,
                end_line=450,
                node_type="FUNCTION",
            ),
        ]

    @patch("repo_utils.method_diff._parse_diff_hunks")
    @pytest.mark.xfail(reason="Mixed hunk classifies entirely-new functions as MODIFIED instead of ADDED", strict=True)
    def test_new_function_in_mixed_hunk_is_added(self, mock_parse, methods, file_path, changes, hunks):
        mock_parse.return_value = hunks

        get_method_statuses_for_file(methods, file_path, changes, Path("/fake/repo"))

        by_name = {m.qualified_name: m.status for m in methods}
        # merge_clusters is entirely new code – it should be ADDED
        assert by_name["cluster_helpers.merge_clusters"] == ChangeStatus.ADDED

    @patch("repo_utils.method_diff._parse_diff_hunks")
    def test_modified_function_in_mixed_hunk_is_modified(self, mock_parse, methods, file_path, changes, hunks):
        mock_parse.return_value = hunks

        get_method_statuses_for_file(methods, file_path, changes, Path("/fake/repo"))

        by_name = {m.qualified_name: m.status for m in methods}
        # build_all_cluster_results overlaps the replaced portion – should be MODIFIED
        assert by_name["cluster_helpers.build_all_cluster_results"] == ChangeStatus.MODIFIED

    @patch("repo_utils.method_diff._parse_diff_hunks")
    def test_unchanged_function_outside_hunk(self, mock_parse, methods, file_path, changes, hunks):
        mock_parse.return_value = hunks

        get_method_statuses_for_file(methods, file_path, changes, Path("/fake/repo"))

        by_name = {m.qualified_name: m.status for m in methods}
        assert by_name["cluster_helpers.get_all_cluster_ids"] == ChangeStatus.UNCHANGED
