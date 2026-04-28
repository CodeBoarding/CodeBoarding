from pathlib import Path

import pytest

from output_generators.agents_md_install import (
    ALL_TARGETS,
    BEGIN_MARKER,
    BLOCK,
    END_MARKER,
    IMPORT_LINE,
    MD_LINK_LINE,
    PLACEHOLDER_CONTENT,
    has_install_marker,
    install_into_repo,
    setup_agents_md,
    setup_agents_md_all,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def digest(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "CODEBOARDING.md"
    src.parent.mkdir()
    src.write_text("# digest\n", encoding="utf-8")
    return src


def test_creates_agents_md_when_missing(repo: Path, digest: Path) -> None:
    install_into_repo(repo, digest)
    assert (repo / "CODEBOARDING.md").read_text() == "# digest\n"
    assert (repo / "AGENTS.md").read_text() == BLOCK + "\n"


def test_prepends_block_when_agents_md_has_no_markers(repo: Path, digest: Path) -> None:
    original = "# Project\n\nExisting guidance.\n"
    (repo / "AGENTS.md").write_text(original, encoding="utf-8")

    install_into_repo(repo, digest)

    result = (repo / "AGENTS.md").read_text()
    assert result.startswith(BLOCK)
    assert result.endswith(original)


def test_refreshes_block_between_existing_markers(repo: Path, digest: Path) -> None:
    stale_block = f"{BEGIN_MARKER}\n@OLD.md\nextra garbage\n{END_MARKER}"
    original = f"# Project\n\nExisting.\n\n{stale_block}\n\nTrailing content.\n"
    (repo / "AGENTS.md").write_text(original, encoding="utf-8")

    install_into_repo(repo, digest)

    result = (repo / "AGENTS.md").read_text()
    assert "@OLD.md" not in result
    assert "extra garbage" not in result
    assert IMPORT_LINE in result
    assert "Trailing content." in result
    assert result.count(BEGIN_MARKER) == 1


def test_noop_when_block_already_current(repo: Path, digest: Path) -> None:
    current = f"# Project\n\n{BLOCK}\n"
    agents_md = repo / "AGENTS.md"
    agents_md.write_text(current, encoding="utf-8")
    mtime_before = agents_md.stat().st_mtime_ns

    install_into_repo(repo, digest)

    assert agents_md.read_text() == current
    assert agents_md.stat().st_mtime_ns == mtime_before


def test_copies_digest_to_repo_root(repo: Path, digest: Path) -> None:
    digest.write_text("# v2\n", encoding="utf-8")
    install_into_repo(repo, digest)
    assert (repo / "CODEBOARDING.md").read_text() == "# v2\n"


def test_handles_agents_md_without_trailing_newline(repo: Path, digest: Path) -> None:
    (repo / "AGENTS.md").write_text("# Project", encoding="utf-8")
    install_into_repo(repo, digest)
    result = (repo / "AGENTS.md").read_text()
    assert result.startswith(BLOCK)
    assert result.endswith("# Project")


def test_has_install_marker_false_when_agents_md_missing(repo: Path) -> None:
    assert has_install_marker(repo) is False


def test_has_install_marker_false_when_no_marker(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("# Plain guidance, no markers here.\n", encoding="utf-8")
    assert has_install_marker(repo) is False


def test_has_install_marker_true_after_install(repo: Path, digest: Path) -> None:
    install_into_repo(repo, digest)
    assert has_install_marker(repo) is True


def test_has_install_marker_false_after_marker_removed(repo: Path, digest: Path) -> None:
    install_into_repo(repo, digest)
    agents_md = repo / "AGENTS.md"
    agents_md.write_text("# Project\n", encoding="utf-8")
    assert has_install_marker(repo) is False


def test_setup_writes_placeholder_and_marker(repo: Path) -> None:
    setup_agents_md(repo)
    assert (repo / "CODEBOARDING.md").read_text() == PLACEHOLDER_CONTENT
    assert has_install_marker(repo) is True


def test_setup_does_not_overwrite_existing_codeboarding_md(repo: Path) -> None:
    (repo / "CODEBOARDING.md").write_text("# user content\n", encoding="utf-8")
    setup_agents_md(repo)
    assert (repo / "CODEBOARDING.md").read_text() == "# user content\n"
    assert has_install_marker(repo) is True


def test_setup_is_idempotent(repo: Path) -> None:
    setup_agents_md(repo)
    setup_agents_md(repo)
    assert (repo / "AGENTS.md").read_text().count(BEGIN_MARKER) == 1


def test_setup_all_writes_to_all_four_targets(repo: Path) -> None:
    touched = setup_agents_md_all(repo)
    assert {p.relative_to(repo).as_posix() for p in touched} == {p for p, _ in ALL_TARGETS}
    for rel, _ in ALL_TARGETS:
        assert (repo / rel).exists(), rel


def test_setup_all_uses_import_line_for_claude_agents(repo: Path) -> None:
    setup_agents_md_all(repo)
    for rel in ("AGENTS.md", "CLAUDE.md"):
        content = (repo / rel).read_text()
        assert IMPORT_LINE in content, rel
        assert MD_LINK_LINE not in content, rel


def test_setup_all_uses_markdown_link_for_copilot_windsurf(repo: Path) -> None:
    setup_agents_md_all(repo)
    for rel in (".github/copilot-instructions.md", ".windsurfrules"):
        content = (repo / rel).read_text()
        assert MD_LINK_LINE in content, rel
        assert IMPORT_LINE not in content, rel


def test_setup_all_creates_github_subdirectory(repo: Path) -> None:
    assert not (repo / ".github").exists()
    setup_agents_md_all(repo)
    assert (repo / ".github" / "copilot-instructions.md").exists()


def test_setup_all_preserves_existing_content_in_all_targets(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("# project rules\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# claude-specific\n", encoding="utf-8")
    (repo / ".github").mkdir()
    (repo / ".github" / "copilot-instructions.md").write_text("# copilot rules\n", encoding="utf-8")
    (repo / ".windsurfrules").write_text("# windsurf rules\n", encoding="utf-8")

    setup_agents_md_all(repo)

    assert "# project rules" in (repo / "AGENTS.md").read_text()
    assert "# claude-specific" in (repo / "CLAUDE.md").read_text()
    assert "# copilot rules" in (repo / ".github" / "copilot-instructions.md").read_text()
    assert "# windsurf rules" in (repo / ".windsurfrules").read_text()


def test_setup_all_idempotent(repo: Path) -> None:
    setup_agents_md_all(repo)
    setup_agents_md_all(repo)
    for rel, _ in ALL_TARGETS:
        assert (repo / rel).read_text().count(BEGIN_MARKER) == 1, rel
