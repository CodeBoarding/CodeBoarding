from pathlib import Path

from agents.agent_responses import MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange
from repo_utils.method_diff import get_method_statuses_for_file
from repo_utils.parsed_diff import ParsedDiffFile, ParsedGitDiff, _parse_patch_text


def test_get_method_statuses_uses_exact_changed_lines_from_context_diff():
    patch_text = """\
diff --git a/src/mod.py b/src/mod.py
index abcdef0..1234567 100644
--- a/src/mod.py
+++ b/src/mod.py
@@ -1,7 +1,7 @@
 def alpha():
     step_one()
-    old_call()
+    new_call()
 
 def beta():
     return 2
"""

    parsed_file = ParsedDiffFile(
        status_code="M",
        file_path="src/mod.py",
        patch_text=patch_text,
        hunks=_parse_patch_text(patch_text),
    )
    changes = ChangeSet(
        changes=[DetectedChange(change_type=ChangeType.MODIFIED, file_path="src/mod.py")],
        base_ref="HEAD",
        target_ref="",
        parsed_diff=ParsedGitDiff(base_ref="HEAD", target_ref="", files=[parsed_file]),
    )
    methods = [
        MethodEntry(qualified_name="mod.alpha", start_line=1, end_line=3, node_type="FUNCTION"),
        MethodEntry(qualified_name="mod.beta", start_line=5, end_line=6, node_type="FUNCTION"),
    ]

    file_status = get_method_statuses_for_file(methods, "src/mod.py", changes, Path("/tmp/repo"))

    assert file_status == ChangeStatus.MODIFIED
    assert methods[0].status == ChangeStatus.MODIFIED
    assert methods[1].status == ChangeStatus.UNCHANGED
