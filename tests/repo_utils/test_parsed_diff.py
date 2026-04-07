from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_utils.parsed_diff import load_parsed_git_diff


def test_load_parsed_git_diff_uses_single_command():
    diff_output = """\
:100644 100644 abcdef0 1234567 M\tsrc/mod.py
diff --git a/src/mod.py b/src/mod.py
index abcdef0..1234567 100644
--- a/src/mod.py
+++ b/src/mod.py
@@ -1,3 +1,3 @@
 def alpha():
-    old_call()
+    new_call()
     return 1
"""

    with patch(
        "repo_utils.parsed_diff.subprocess.run",
        return_value=CompletedProcess(args=["git", "diff"], returncode=0, stdout=diff_output, stderr=""),
    ) as run_mock:
        parsed = load_parsed_git_diff(Path("/tmp/repo"), "HEAD", "")

    run_mock.assert_called_once()
    command = run_mock.call_args.args[0]
    assert command == [
        "git",
        "diff",
        "--raw",
        "-U3",
        "-M",
        "-C",
        "--find-renames=50%",
        "HEAD",
    ]
    assert len(parsed.files) == 1
    assert parsed.files[0].file_path == "src/mod.py"
    assert parsed.files[0].status_code == "M"
    assert len(parsed.files[0].hunks) == 1


def test_load_parsed_git_diff_tracks_rename_metadata():
    diff_output = """\
:100644 100644 abcdef0 1234567 R100\told.py\tnew.py
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
"""

    with patch(
        "repo_utils.parsed_diff.subprocess.run",
        return_value=CompletedProcess(args=["git", "diff"], returncode=0, stdout=diff_output, stderr=""),
    ):
        parsed = load_parsed_git_diff(Path("/tmp/repo"), "HEAD~1", "HEAD")

    assert len(parsed.files) == 1
    assert parsed.files[0].file_path == "new.py"
    assert parsed.files[0].old_path == "old.py"
    assert parsed.files[0].similarity == 100
