from pathlib import Path

from diagram_analysis.diagram_generator import DiagramGenerator
from repo_utils.change_detector import ChangeSet


def _host(changes: ChangeSet | None, repo: Path) -> DiagramGenerator:
    """A DiagramGenerator carrying only what ``_changed_files_for_static_analysis`` reads."""
    gen = DiagramGenerator.__new__(DiagramGenerator)
    gen.changes = changes
    gen.repo_location = repo
    return gen


def test_none_changeset_returns_none(tmp_path: Path) -> None:
    """No ChangeSet -> full run -> git scoping (None)."""
    assert _host(None, tmp_path)._changed_files_for_static_analysis() is None


def test_empty_changeset_returns_empty_set_not_none(tmp_path: Path) -> None:
    """Incremental with nothing changed -> re-LSP zero files, not a git-fallback full re-LSP."""
    host = _host(ChangeSet.from_changed_files(added=[], modified=[], deleted=[]), tmp_path)
    assert host._changed_files_for_static_analysis() == set()


def test_populated_changeset_returns_absolute_paths(tmp_path: Path) -> None:
    changes = ChangeSet.from_changed_files(added=["a.py"], modified=["b.py"], deleted=[])
    result = _host(changes, tmp_path)._changed_files_for_static_analysis()
    assert result == {(tmp_path / "a.py").resolve(), (tmp_path / "b.py").resolve()}
