"""Tests for codeboarding_web.watcher.RepoWatcher path filtering."""

from pathlib import Path
from codeboarding_web.watcher import RepoWatcher


def _w(tmp_path: Path) -> RepoWatcher:
    return RepoWatcher(repo_path=tmp_path, output_dir=tmp_path / ".codeboarding", on_change=lambda: None)


def test_watches_source_file(tmp_path: Path) -> None:
    assert _w(tmp_path)._should_watch(str(tmp_path / "pkg" / "mod.py")) is True


def test_ignores_output_dir(tmp_path: Path) -> None:
    w = _w(tmp_path)
    assert w._should_watch(str(tmp_path / ".codeboarding" / "analysis.json")) is False


def test_ignores_non_source(tmp_path: Path) -> None:
    assert _w(tmp_path)._should_watch(str(tmp_path / "README.md")) is False


def test_ignores_git_dir(tmp_path: Path) -> None:
    assert _w(tmp_path)._should_watch(str(tmp_path / ".git" / "index")) is False
