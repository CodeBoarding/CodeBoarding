"""Tests for ``FileChange.classify_method_statuses``."""

import pytest

from agents.agent_responses import MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import DiffHunk, FileChange


def _make_file_change(file_path: str, hunks: list[DiffHunk]) -> FileChange:
    return FileChange(status_code="M", file_path=file_path, hunks=hunks)


class TestNewFunctionInMixedHunk:
    """A hunk that modifies a few existing lines AND adds many new lines should
    mark a brand-new function (fully inside the added portion) as ADDED, not
    MODIFIED.

    Real-world example: ``static_analyzer/cluster_helpers.py`` had a hunk
    ``@@ -42,10 +69,367 @@`` which modified 10 old lines into 367 new lines.
    A purely header-based classifier (old_count=10 > 0) puts the whole 69-435
    range into ``changed_ranges``; walking the body flushes per segment and
    correctly reports the tail ``+``-only run as ``added_ranges``.
    """

    @pytest.fixture
    def file_path(self) -> str:
        return "static_analyzer/cluster_helpers.py"

    @pytest.fixture
    def mixed_hunk(self) -> DiffHunk:
        # Lines 69-78 replace the old 42-51 window; lines 79-435 are brand-new.
        lines: list[str] = []
        for _ in range(10):
            lines.append("-old line")
        for _ in range(10):
            lines.append("+replacement line")
        for _ in range(357):
            lines.append("+brand new line")
        return DiffHunk(old_start=42, old_count=10, new_start=69, new_count=367, lines=lines)

    @pytest.fixture
    def methods(self) -> list[MethodEntry]:
        return [
            MethodEntry(
                qualified_name="cluster_helpers.build_all_cluster_results",
                start_line=69,
                end_line=110,
                node_type="FUNCTION",
            ),
            MethodEntry(
                qualified_name="cluster_helpers.merge_clusters",
                start_line=385,
                end_line=424,
                node_type="FUNCTION",
            ),
            MethodEntry(
                qualified_name="cluster_helpers.get_all_cluster_ids",
                start_line=440,
                end_line=450,
                node_type="FUNCTION",
            ),
        ]

    def test_new_function_in_mixed_hunk_is_added(self, methods, file_path, mixed_hunk):
        by_name = _make_file_change(file_path, [mixed_hunk]).classify_method_statuses(methods)
        assert by_name["cluster_helpers.merge_clusters"] == ChangeStatus.ADDED

    def test_modified_function_in_mixed_hunk_is_modified(self, methods, file_path, mixed_hunk):
        by_name = _make_file_change(file_path, [mixed_hunk]).classify_method_statuses(methods)
        assert by_name["cluster_helpers.build_all_cluster_results"] == ChangeStatus.MODIFIED

    def test_unchanged_function_outside_hunk(self, methods, file_path, mixed_hunk):
        by_name = _make_file_change(file_path, [mixed_hunk]).classify_method_statuses(methods)
        assert by_name["cluster_helpers.get_all_cluster_ids"] == ChangeStatus.UNCHANGED
