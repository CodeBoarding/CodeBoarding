from pathlib import Path

import pytest

from agents.content_hash import hash_repo_source_files
from diagram_analysis.io_utils import write_fingerprint
from repo_utils.fingerprint_diff import (
    BaselineUnavailableError,
    detect_changes_from_fingerprint,
    diff_file_maps,
)


def test_diff_file_maps_classifies_added_modified_deleted() -> None:
    old = {"a.py": "h1", "b.py": "h2", "c.py": "h3"}
    new = {"a.py": "h1", "b.py": "CHANGED", "d.py": "h4"}
    added, modified, deleted = diff_file_maps(old, new)
    assert added == ["d.py"]
    assert modified == ["b.py"]
    assert deleted == ["c.py"]


def test_detect_reports_added_files_against_sidecar(tmp_path: Path) -> None:
    """A brand-new file must surface as added — the whole-tree sidecar covers it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "existing.py").write_text("x = 1\n")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    write_fingerprint(output_dir, hash_repo_source_files(repo))

    (repo / "new_file.py").write_text("y = 2\n")
    changes = detect_changes_from_fingerprint(repo, output_dir)

    assert "new_file.py" in changes.added_files


def test_detect_raises_when_sidecar_missing(tmp_path: Path) -> None:
    """No whole-tree fingerprint -> raise, never silently no-op against component hashes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    with pytest.raises(BaselineUnavailableError, match="fingerprint sidecar"):
        detect_changes_from_fingerprint(repo, output_dir)
