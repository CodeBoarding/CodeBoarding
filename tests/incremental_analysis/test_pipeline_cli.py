from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    assign_component_ids,
)
from main import build_parser, main as cli_main
from incremental_analysis.models import (
    IncrementalRunResult,
    IncrementalSummary,
    IncrementalSummaryKind,
)
from incremental_analysis.payload import (
    RequiresFullAnalysisPayload,
    IncrementalCompletedPayload,
    NoChangesPayload,
)
from incremental_analysis.pipeline import IncrementalInputs, run_incremental_pipeline
from diagram_analysis.run_metadata import last_successful_commit, write_full_run_metadata
from repo_utils.git_ops import worktree_has_changes
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.node import Node
from analysis_format.io_utils import save_analysis
from repo_utils.change_detector import ChangeSet


def _make_inputs(
    repo_path: Path,
    output_dir: Path,
    static_analysis: StaticAnalysisResults | None = None,
) -> IncrementalInputs:
    """Build a typed ``IncrementalInputs`` fixture for pipeline tests."""
    return IncrementalInputs(
        repo_path=repo_path,
        output_dir=output_dir,
        repo_name="repo",
        prepare_static_analysis=lambda: static_analysis,
        build_file_coverage_summary=lambda: None,
        write_file_coverage=lambda: None,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_analysis(file_path: str, method: MethodEntry, repo_name: str = "repo") -> AnalysisInsights:
    component = Component(
        name="Core",
        description="Core",
        key_entities=[],
        file_methods=[FileMethodGroup(file_path=file_path, methods=[method])],
    )
    analysis = AnalysisInsights(
        description="analysis",
        components=[component],
        components_relations=[],
        files={file_path: FileEntry(methods=[method])},
    )
    assign_component_ids(analysis)
    return analysis


def _make_static_analysis(file_path: str, qualified_name: str, start_line: int, end_line: int) -> StaticAnalysisResults:
    result = StaticAnalysisResults()
    result.add_references(
        "python",
        [
            Node(
                fully_qualified_name=qualified_name,
                node_type=NodeType.FUNCTION,
                file_path=file_path,
                line_start=start_line,
                line_end=end_line,
            )
        ],
    )
    result.add_source_files("python", [file_path])
    return result


def test_last_successful_commit_falls_back_to_analysis_metadata(tmp_path: Path) -> None:
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir()
    (output_dir / "analysis.json").write_text(
        json.dumps({"metadata": {"commit_hash": "abc123"}}, indent=2),
        encoding="utf-8",
    )

    assert last_successful_commit(output_dir) == "abc123"


def test_last_successful_commit_rejects_dirty_full_run_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    source_file = repo / "app.py"
    source_file.write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    source_file.write_text("print('v2')\n", encoding="utf-8")
    output_dir = repo / ".codeboarding"
    write_full_run_metadata(output_dir, repo, analysis_path=output_dir / "analysis.json")

    assert last_successful_commit(output_dir) is None


def test_worktree_dirty_check_ignores_codeboarding_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    source_file = repo / "app.py"
    source_file.write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    (output_dir / "analysis.json").write_text("{}", encoding="utf-8")

    assert worktree_has_changes(repo, exclude_patterns=(".codeboarding",)) is False


def test_run_incremental_pipeline_returns_full_required_without_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output_dir = repo / ".codeboarding"
    output_dir.mkdir()

    payload = run_incremental_pipeline(_make_inputs(repo, output_dir), base_ref="", target_ref="")

    assert isinstance(payload, RequiresFullAnalysisPayload)
    assert payload.requires_full_analysis is True
    assert "No existing analysis.json" in payload.message


def test_run_incremental_analysis_uses_explicit_base_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    source_file = repo / "src" / "utils.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    baseline_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    method = MethodEntry(
        qualified_name="src.utils.alpha",
        start_line=1,
        end_line=2,
        node_type="FUNCTION",
    )
    analysis = _make_analysis("src/utils.py", method)
    save_analysis(analysis=analysis, output_dir=output_dir, repo_name="repo", commit_hash=baseline_commit)

    source_file.write_text(
        "def alpha():\n    value = 1\n    return value\n",
        encoding="utf-8",
    )

    fake_static = _make_static_analysis("src/utils.py", "src.utils.alpha", 1, 3)
    fake_path = output_dir / "analysis.json"

    inputs = _make_inputs(repo, output_dir, static_analysis=fake_static)

    with (
        patch("incremental_analysis.pipeline.initialize_llms", return_value=(MagicMock(), MagicMock())),
        patch("incremental_analysis.pipeline.run_trace") as run_trace_mock,
        patch("incremental_analysis.pipeline.derive_patch_scopes", return_value=[]),
        patch("incremental_analysis.pipeline.save_analysis", return_value=fake_path),
    ):
        run_trace_mock.return_value = MagicMock(
            visited_methods=[],
            impacted_methods=[],
            non_traceable_files=[],
            disconnected_files=[],
            unresolved_frontier=[],
        )
        payload = run_incremental_pipeline(inputs, base_ref=baseline_commit, target_ref="")

    assert isinstance(payload, IncrementalCompletedPayload)
    assert payload.base_ref == baseline_commit
    assert payload.requires_full_analysis is False
    assert payload.incremental_delta.file_deltas


def test_run_incremental_pipeline_requires_full_when_git_diff_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    method = MethodEntry(
        qualified_name="src.utils.alpha",
        start_line=1,
        end_line=2,
        node_type="FUNCTION",
    )
    save_analysis(
        analysis=_make_analysis("src/utils.py", method),
        output_dir=output_dir,
        repo_name="repo",
        commit_hash="base",
    )

    with patch(
        "incremental_analysis.pipeline.detect_changes",
        return_value=ChangeSet(base_ref="base", target_ref="", error="bad object base"),
    ):
        payload = run_incremental_pipeline(_make_inputs(repo, output_dir), base_ref="base", target_ref="")

    assert isinstance(payload, RequiresFullAnalysisPayload)
    assert payload.requires_full_analysis is True
    assert "Git diff failed" in payload.message
    assert not (output_dir / "incremental_run_metadata.json").exists()


def test_incremental_main_emits_stable_json_stdout(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    analysis_path = output_dir / "analysis.json"
    analysis_path.write_text("{}", encoding="utf-8")

    fake_payload = NoChangesPayload(
        base_ref="abc",
        target_ref="",
        resolved_target_commit="abc",
        change_set=ChangeSet(base_ref="abc", target_ref=""),
        metadata_path=output_dir / "incremental_run_metadata.json",
        analysis_path=analysis_path,
    )

    run_context_instance = MagicMock(run_id="test", log_path="test.log")
    with (
        patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"),
        patch(
            "codeboarding_cli.commands.incremental_analysis.run_incremental",
            return_value=fake_payload,
        ),
        patch("codeboarding_cli.commands.incremental_analysis.RunContext") as run_context_cls,
    ):
        run_context_cls.resolve.return_value = run_context_instance
        # --base-ref keeps the CLI out of the last_successful_commit fallback
        cli_main(["incremental", "--local", str(repo), "--base-ref", "abc"])

    stdout = capsys.readouterr().out
    emitted = json.loads(stdout)
    # Keys are emitted sort_keys=True, so we assert lexicographic ordering
    assert list(emitted.keys()) == sorted(emitted.keys())
    assert emitted["mode"] == "incremental"
    assert emitted["baseRef"] == "abc"
    assert emitted["targetRef"] == "WORKTREE"
    assert emitted["requiresFullAnalysis"] is False
    assert emitted["summary"]["kind"] == IncrementalSummaryKind.NO_CHANGES.value
    run_context_instance.finalize.assert_called_once()


def test_incremental_cli_bails_out_when_no_base_ref_and_no_metadata(tmp_path: Path, capsys) -> None:
    """The CLI resolves base_ref before invoking the workflow; with no prior
    metadata and no ``--base-ref`` it must emit REQUIRES_FULL_ANALYSIS directly
    (workflow is never called)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    with (
        patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"),
        patch("codeboarding_cli.commands.incremental_analysis.run_incremental") as run_mock,
        patch("codeboarding_cli.commands.incremental_analysis.RunContext"),
    ):
        cli_main(["incremental", "--local", str(repo)])

    run_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["requiresFullAnalysis"] is True
    assert "No prior incremental metadata" in payload["summary"]["message"]


def test_incremental_mode_api_key_missing_payload(tmp_path: Path, capsys) -> None:
    from agents.llm_config import LLMConfigError

    repo = tmp_path / "repo"
    repo.mkdir()
    with patch(
        "codeboarding_cli.commands.incremental_analysis.bootstrap_environment",
        side_effect=LLMConfigError("No API key configured"),
    ):
        cli_main(["incremental", "--local", str(repo)])
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload == {
        "mode": "incremental",
        "error": "No API key configured",
        "kind": "api_key_missing",
    }


def test_run_incremental_analysis_requires_full_on_rename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    source_file = repo / "src" / "utils.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    baseline_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    method = MethodEntry(
        qualified_name="src.utils.alpha",
        start_line=1,
        end_line=2,
        node_type="FUNCTION",
    )
    save_analysis(
        analysis=_make_analysis("src/utils.py", method),
        output_dir=output_dir,
        repo_name="repo",
        commit_hash=baseline_commit,
    )

    _git(repo, "mv", "src/utils.py", "src/helpers.py")

    payload = run_incremental_pipeline(_make_inputs(repo, output_dir), base_ref=baseline_commit, target_ref="")

    assert isinstance(payload, RequiresFullAnalysisPayload)
    assert payload.requires_full_analysis is True
    assert "rename" in payload.message.lower()
    assert not (output_dir / "incremental_run_metadata.json").exists()


def test_run_incremental_pipeline_rejects_target_ref_not_checked_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    source_file = repo / "src" / "utils.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    baseline_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    source_file.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2")
    head_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_commit != baseline_commit

    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    method = MethodEntry(
        qualified_name="src.utils.alpha",
        start_line=1,
        end_line=2,
        node_type="FUNCTION",
    )
    save_analysis(
        analysis=_make_analysis("src/utils.py", method),
        output_dir=output_dir,
        repo_name="repo",
        commit_hash=baseline_commit,
    )

    payload = run_incremental_pipeline(
        _make_inputs(repo, output_dir),
        base_ref=baseline_commit,
        target_ref=baseline_commit,  # HEAD is at v2, not baseline
    )

    assert isinstance(payload, RequiresFullAnalysisPayload)
    assert payload.requires_full_analysis is True
    assert "does not match the current checkout" in payload.message
    assert not (output_dir / "incremental_run_metadata.json").exists()


def test_run_incremental_pipeline_rejects_target_ref_with_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    source_file = repo / "src" / "utils.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    head_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Dirty the worktree after committing.
    source_file.write_text("def alpha():\n    return 99\n", encoding="utf-8")

    output_dir = repo / ".codeboarding"
    output_dir.mkdir()
    method = MethodEntry(
        qualified_name="src.utils.alpha",
        start_line=1,
        end_line=2,
        node_type="FUNCTION",
    )
    save_analysis(
        analysis=_make_analysis("src/utils.py", method),
        output_dir=output_dir,
        repo_name="repo",
        commit_hash=head_commit,
    )

    payload = run_incremental_pipeline(
        _make_inputs(repo, output_dir),
        base_ref=head_commit,
        target_ref=head_commit,  # HEAD matches, but worktree is dirty
    )

    assert isinstance(payload, RequiresFullAnalysisPayload)
    assert payload.requires_full_analysis is True
    assert "dirty worktree" in payload.message
    assert not (output_dir / "incremental_run_metadata.json").exists()


def test_incremental_subcommand_exposes_expected_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "incremental",
            "--local",
            "/tmp/repo",
            "--output-dir",
            "/tmp/out",
            "--project-name",
            "repo",
            "--depth-level",
            "2",
            "--base-ref",
            "HEAD~1",
            "--target-ref",
            "HEAD",
            "--binary-location",
            "/tmp/bin",
            "--enable-monitoring",
        ]
    )
    assert args.command == "incremental"
    assert args.base_ref == "HEAD~1"
    assert args.target_ref == "HEAD"
    assert args.depth_level == 2
    assert args.enable_monitoring is True
