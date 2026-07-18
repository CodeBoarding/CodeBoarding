"""Warm-start static analysis should only change analysis for edited files."""

from pathlib import Path
import subprocess

import pytest

from static_analyzer import get_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults


def _git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _init_git_repo(repo_path: Path) -> str:
    _git(["init"], cwd=repo_path)
    _git(["config", "user.email", "test@example.com"], cwd=repo_path)
    _git(["config", "user.name", "Test User"], cwd=repo_path)
    _git(["add", "."], cwd=repo_path)
    _git(["commit", "-m", "base"], cwd=repo_path)
    return _git(["rev-parse", "HEAD"], cwd=repo_path)


def _apply_changes(repo_path: Path, change_name: str, files: dict[str, str]) -> set[str]:
    changed_files: set[str] = set()
    for relative_path, content in files.items():
        path = repo_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        changed_files.add(relative_path)
    assert changed_files, f"{change_name} must touch at least one file"
    return changed_files


def _rel(path: str, repo_path: Path) -> str:
    return Path(path).resolve().relative_to(repo_path.resolve()).as_posix()


def _node_tuple(node, repo_path: Path) -> tuple:
    return (
        node.node_id,
        _rel(node.file_path, repo_path),
        node.line_start,
        node.line_end,
        node.col_start,
        int(node.symbol_type),
    )


def _static_analysis_by_file(result: StaticAnalysisResults, repo_path: Path) -> dict[str, frozenset[tuple]]:
    by_file: dict[str, set[tuple]] = {}

    def add(file_path: str, value: tuple) -> None:
        by_file.setdefault(_rel(file_path, repo_path), set()).add(value)

    for language in result.get_languages():
        for source_file in result.get_source_files(language):
            add(source_file, ("source", language.value))

        try:
            cfg = result.get_program_graph(language)
        except ValueError:
            continue
        for node in cfg.symbol_nodes():
            add(node.file_path, ("node", *_node_tuple(node, repo_path)))
        for edge in cfg.call_edges():
            add(
                cfg.nodes[edge.source].file_path,
                (
                    "edge",
                    language.value,
                    edge.source,
                    edge.target,
                    _rel(cfg.nodes[edge.target].file_path, repo_path),
                ),
            )

        for ref in result.iter_reference_nodes(language):
            add(ref.file_path, ("reference", *_node_tuple(ref, repo_path)))

        try:
            hierarchy = result.get_hierarchy(language)
        except ValueError:
            hierarchy = {}
        for class_name, info in hierarchy.items():
            file_path = info.get("file_path")
            if file_path:
                add(
                    file_path,
                    (
                        "class",
                        class_name,
                        tuple(sorted(info.get("superclasses", []))),
                        tuple(sorted(info.get("subclasses", []))),
                    ),
                )

    return {file_path: frozenset(items) for file_path, items in by_file.items()}


def _changed_analysis_files(
    old_result: StaticAnalysisResults, new_result: StaticAnalysisResults, repo_path: Path
) -> set[str]:
    old_by_file = _static_analysis_by_file(old_result, repo_path)
    new_by_file = _static_analysis_by_file(new_result, repo_path)
    return {
        file_path
        for file_path in set(old_by_file) | set(new_by_file)
        if old_by_file.get(file_path) != new_by_file.get(file_path)
    }


def _edge_set(result: StaticAnalysisResults, repo_path: Path) -> set[tuple[str, str, str, str]]:
    edges: set[tuple[str, str, str, str]] = set()
    for language in result.get_languages():
        try:
            cfg = result.get_program_graph(language)
        except ValueError:
            continue
        for edge in cfg.call_edges():
            edges.add(
                (
                    edge.source,
                    edge.target,
                    _rel(cfg.nodes[edge.source].file_path, repo_path),
                    _rel(cfg.nodes[edge.target].file_path, repo_path),
                )
            )
    return edges


def _boundary_edges(edges: set[tuple[str, str, str, str]], changed_files: set[str]) -> set[tuple[str, str, str, str]]:
    return {edge for edge in edges if edge[2] in changed_files or edge[3] in changed_files}


def _assert_boundary_edges_match_full(
    warm_static_analysis: StaticAnalysisResults,
    full_static_analysis: StaticAnalysisResults,
    repo_path: Path,
    changed_files: set[str],
) -> None:
    expected_edges = {
        ("pkg.app.main", "pkg.service.service", "pkg/app.py", "pkg/service.py"),
        ("pkg.service.service", "pkg.util.helper", "pkg/service.py", "pkg/util.py"),
        ("pkg.new_worker.run_new", "pkg.util.helper", "pkg/new_worker.py", "pkg/util.py"),
    }
    warm_edges = _edge_set(warm_static_analysis, repo_path)
    full_edges = _edge_set(full_static_analysis, repo_path)
    warm_boundary = _boundary_edges(warm_edges, changed_files)
    full_boundary = _boundary_edges(full_edges, changed_files)

    assert expected_edges <= full_boundary
    assert expected_edges <= warm_boundary
    assert warm_boundary == full_boundary


def _removed_cross_boundary_edges(
    old_result: StaticAnalysisResults,
    new_result: StaticAnalysisResults,
    repo_path: Path,
    changed_files: set[str],
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    removed_edges = _edge_set(old_result, repo_path) - _edge_set(new_result, repo_path)
    inbound = {edge for edge in removed_edges if edge[2] not in changed_files and edge[3] in changed_files}
    outbound = {edge for edge in removed_edges if edge[2] in changed_files and edge[3] not in changed_files}
    return inbound, outbound


def _assert_only_expected_analysis_files_changed(
    changed_analysis_files: set[str], expected_changed_files: set[str]
) -> None:
    unexpected = changed_analysis_files - expected_changed_files
    if unexpected:
        pytest.fail(
            "warm-start static analysis changed files outside the edited files\n"
            f"expected_only={sorted(expected_changed_files)!r}\n"
            f"actual_changed_static_analysis_files={sorted(changed_analysis_files)!r}\n"
            f"unexpected={sorted(unexpected)!r}"
        )


def _assert_no_persisted_cross_boundary_edges_were_dropped(
    inbound: set[tuple[str, str, str, str]],
    outbound: set[tuple[str, str, str, str]],
    full_static_analysis: StaticAnalysisResults,
    repo_path: Path,
) -> None:
    full_edges = _edge_set(full_static_analysis, repo_path)
    persisted_inbound = inbound & full_edges
    persisted_outbound = outbound & full_edges
    if persisted_inbound or persisted_outbound:
        pytest.fail(
            "warm-start static analysis dropped cross-boundary edges that still exist in a full analysis\n"
            f"persisted_inbound_unchanged_to_changed={len(persisted_inbound)} "
            f"sample={sorted(persisted_inbound)[:10]!r}\n"
            f"persisted_outbound_changed_to_unchanged={len(persisted_outbound)} "
            f"sample={sorted(persisted_outbound)[:10]!r}\n"
            f"stale_removed_inbound={len(inbound - full_edges)}\n"
            f"stale_removed_outbound={len(outbound - full_edges)}"
        )


def _assert_graph_changes(
    old_static_analysis: StaticAnalysisResults,
    new_static_analysis: StaticAnalysisResults,
    full_static_analysis: StaticAnalysisResults,
    repo_path: Path,
    changed_files: set[str],
) -> None:
    changed_analysis_files = _changed_analysis_files(old_static_analysis, new_static_analysis, repo_path)
    removed_inbound, removed_outbound = _removed_cross_boundary_edges(
        old_static_analysis, new_static_analysis, repo_path, changed_files
    )

    _assert_no_persisted_cross_boundary_edges_were_dropped(
        removed_inbound, removed_outbound, full_static_analysis, repo_path
    )
    _assert_only_expected_analysis_files_changed(changed_analysis_files, changed_files)


def _write_boundary_project(repo_path: Path) -> None:
    (repo_path / "pkg").mkdir(parents=True)
    (repo_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo_path / "pkg" / "util.py").write_text(
        """def helper():
    return "ok"


def normalize(value):
    return value.strip()
""",
        encoding="utf-8",
    )
    (repo_path / "pkg" / "service.py").write_text(
        """from pkg.util import helper


def service():
    return helper()
""",
        encoding="utf-8",
    )
    (repo_path / "pkg" / "workflow.py").write_text(
        """from pkg.util import normalize


def workflow(value):
    return normalize(value)
""",
        encoding="utf-8",
    )
    (repo_path / "pkg" / "app.py").write_text(
        """from pkg.service import service
from pkg.workflow import workflow


def main(value):
    first = service()
    return workflow(first + value)
""",
        encoding="utf-8",
    )


@pytest.mark.integration
@pytest.mark.python_lang
def test_static_analysis_updates_cross_edges(tmp_path: Path) -> None:
    repo_path = tmp_path / "boundary_project"
    repo_path.mkdir()
    _write_boundary_project(repo_path)
    base_sha = _init_git_repo(repo_path)
    artifact_dir = tmp_path / "warm_cache"
    fresh_dir = tmp_path / "fresh_cache"

    old_static_analysis = get_static_analysis(repo_path, artifact_dir, skip_cache=True, source_sha=base_sha)

    change_1_files = _apply_changes(
        repo_path,
        "change_1_service_keeps_inbound_and_outbound_edges",
        {
            "pkg/service.py": """from pkg.util import helper


def service():
    value = helper()
    return value
"""
        },
    )
    change_2_files = _apply_changes(
        repo_path,
        "change_2_workflow_keeps_inbound_and_outbound_edges",
        {
            "pkg/workflow.py": """from pkg.util import normalize


def workflow(value):
    cleaned = normalize(value)
    return cleaned
"""
        },
    )
    change_3_files = _apply_changes(
        repo_path,
        "change_3_new_file_adds_outbound_edge_to_existing_method",
        {
            "pkg/new_worker.py": """from pkg.util import helper


def run_new():
    return helper()
"""
        },
    )
    changed_files = change_1_files | change_2_files | change_3_files

    new_static_analysis = get_static_analysis(repo_path, artifact_dir, skip_cache=False, source_sha="after-two-changes")

    full_static_analysis = get_static_analysis(repo_path, fresh_dir, skip_cache=True, source_sha="after-two-changes")

    _assert_boundary_edges_match_full(new_static_analysis, full_static_analysis, repo_path, changed_files)

    _assert_graph_changes(
        old_static_analysis,
        new_static_analysis,
        full_static_analysis,
        repo_path,
        changed_files,
    )
