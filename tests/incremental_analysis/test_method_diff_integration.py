import shutil
import subprocess
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    assign_component_ids,
)
from diagram_analysis.incremental_updater import IncrementalUpdater
from repo_utils.diff_parser import detect_changes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _build_analysis(methods_by_file: dict[str, list[MethodEntry]]) -> AnalysisInsights:
    files: dict[str, FileEntry] = {}
    groups: list[FileMethodGroup] = []
    for file_path, methods in methods_by_file.items():
        ordered = sorted(methods, key=lambda m: (m.start_line, m.end_line, m.qualified_name))
        files[file_path] = FileEntry(methods=ordered)
        groups.append(FileMethodGroup(file_path=file_path, methods=ordered))

    component = Component(name="Core", description="Core component", key_entities=[], file_methods=groups)
    analysis = AnalysisInsights(description="integration", components=[component], components_relations=[], files=files)
    assign_component_ids(analysis)
    return analysis


@pytest.mark.integration
def test_incremental_delta_reports_added_modified_deleted_in_single_file():
    temp_dir = Path(tempfile.mkdtemp(prefix="cb_core_incremental_"))
    try:
        repo = temp_dir / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")

        baseline = dedent(
            """\
            def alpha() -> int:
                return 1


            def beta() -> int:
                return 2


            def gamma() -> int:
                return 3
            """
        )
        file_path = repo / "src" / "utils.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(baseline)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "baseline")

        old_methods = {
            "src/utils.py": [
                MethodEntry(qualified_name="src.utils.alpha", start_line=1, end_line=2, node_type="FUNCTION"),
                MethodEntry(qualified_name="src.utils.beta", start_line=5, end_line=6, node_type="FUNCTION"),
                MethodEntry(qualified_name="src.utils.gamma", start_line=9, end_line=10, node_type="FUNCTION"),
            ]
        }
        analysis = _build_analysis(old_methods)

        changed = dedent(
            """\
            def alpha() -> int:
                value = 1
                return value


            def gamma() -> int:
                return 3


            def delta() -> int:
                return 4
            """
        )
        file_path.write_text(changed)

        current_methods = {
            "src/utils.py": [
                MethodEntry(qualified_name="src.utils.alpha", start_line=1, end_line=3, node_type="FUNCTION"),
                MethodEntry(qualified_name="src.utils.gamma", start_line=6, end_line=7, node_type="FUNCTION"),
                MethodEntry(qualified_name="src.utils.delta", start_line=10, end_line=11, node_type="FUNCTION"),
            ]
        }

        def resolver(rel_path: str) -> list[MethodEntry]:
            return current_methods.get(rel_path, [])

        change_set = detect_changes(repo, "HEAD", target_ref="")
        updater = IncrementalUpdater(
            analysis,
            symbol_resolver=resolver,
            repo_dir=repo,
        )
        delta = updater.compute_delta(
            added_files=list(change_set.added_files),
            modified_files=list(change_set.modified_files),
            deleted_files=list(change_set.deleted_files),
            changes=change_set,
        )

        file_delta = next(fd for fd in delta.file_deltas if fd.file_path == "src/utils.py")
        assert file_delta.file_status == "modified"
        assert {m.qualified_name: m.change_type for m in file_delta.added_methods} == {"src.utils.delta": "added"}
        assert {m.qualified_name: m.change_type for m in file_delta.modified_methods} == {"src.utils.alpha": "modified"}
        assert {m.qualified_name: m.change_type for m in file_delta.deleted_methods} == {"src.utils.beta": "deleted"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
