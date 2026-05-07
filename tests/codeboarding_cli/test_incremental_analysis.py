from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from main import build_parser, main


@pytest.fixture
def stub_run_incremental(tmp_path: Path):
    """Patch the chain so ``run_from_args`` reaches ``run_incremental`` without running it.

    Yields ``(run_incremental_mock, load_analysis_commit_hash_mock, get_current_commit_mock, resolve_ref_mock)``
    so individual tests can shape baseline / source-SHA resolution. The CLI
    default for ``--base-ref`` is the user's analysis metadata commit hash —
    *not* the wrapper-owned ``snapshotCommit`` field, which is meaningful
    only inside the extension/snapshot-worktree flow.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"))
        rc = stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.RunContext"))
        rc.resolve.return_value.run_id = "rid"
        rc.resolve.return_value.log_path = "logs/run.log"
        last = stack.enter_context(
            patch(
                "codeboarding_cli.commands.incremental_analysis.load_analysis_commit_hash", return_value="last-success"
            )
        )
        head = stack.enter_context(
            patch("codeboarding_cli.commands.incremental_analysis.get_current_commit", return_value="current-head")
        )
        resolve = stack.enter_context(
            patch("codeboarding_cli.commands.incremental_analysis.resolve_ref", return_value="resolved-head")
        )
        ri = stack.enter_context(
            patch(
                "codeboarding_cli.commands.incremental_analysis.run_incremental",
                return_value=tmp_path / "analysis.json",
            )
        )
        yield ri, last, head, resolve


def test_incremental_passes_base_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    """Explicit --base-ref wins over the analysis-metadata commit hash."""
    ri, last, head, resolve = stub_run_incremental

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc123"])

    last.assert_not_called()
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "abc123"
    # No --target-ref: CLI resolves via get_current_commit.
    head.assert_called_once()
    resolve.assert_called_once_with(tmp_path, "current-head")
    assert kwargs["target_ref"] == "current-head"
    assert kwargs["source_sha"] == "resolved-head"


def test_incremental_passes_target_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    """Explicit --target-ref wins over get_current_commit."""
    ri, _last, head, resolve = stub_run_incremental

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc", "--target-ref", "HEAD"])

    head.assert_not_called()
    resolve.assert_called_once_with(tmp_path, "HEAD")
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "abc"
    assert kwargs["target_ref"] == "HEAD"
    assert kwargs["source_sha"] == "resolved-head"


def test_incremental_empty_target_ref_diffs_worktree(tmp_path: Path, stub_run_incremental) -> None:
    """--target-ref "" opts in to a worktree diff and stamps source_sha from HEAD."""
    ri, _last, head, resolve = stub_run_incremental

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc", "--target-ref", ""])

    head.assert_called_once()
    resolve.assert_not_called()
    kwargs = ri.call_args.kwargs
    assert kwargs["target_ref"] == ""
    assert kwargs["source_sha"] == "current-head"


def test_incremental_no_flags_resolves_from_metadata_and_head(tmp_path: Path, stub_run_incremental) -> None:
    """No flags: CLI resolves base from analysis metadata commit and target from current HEAD."""
    ri, last, head, resolve = stub_run_incremental

    main(["incremental", "--local", str(tmp_path)])

    last.assert_called_once()
    head.assert_called_once()
    resolve.assert_called_once_with(tmp_path, "current-head")
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "last-success"
    assert kwargs["target_ref"] == "current-head"
    assert kwargs["source_sha"] == "resolved-head"


def test_incremental_default_does_not_use_snapshot_commit(tmp_path: Path, stub_run_incremental) -> None:
    """CLI must not pull ``snapshotCommit`` from analysis.json as the default base.

    The wrapper-owned ``snapshotCommit`` field is the analysis-snapshot
    commit; the standalone CLI's default baseline is the user-facing
    analysis metadata commit hash.
    """
    ri, last, _head, _resolve = stub_run_incremental

    main(["incremental", "--local", str(tmp_path)])

    # Module no longer imports load_snapshot_commit; it should be unreachable
    # from the CLI namespace.
    import codeboarding_cli.commands.incremental_analysis as mod

    assert not hasattr(mod, "load_snapshot_commit")
    last.assert_called_once()
    assert ri.call_args.kwargs["base_ref"] == "last-success"


def test_incremental_no_baseline_short_circuits(tmp_path: Path, stub_run_incremental) -> None:
    """No --base-ref and no analysis-metadata commit: emit error, never call run_incremental."""
    ri, last, _head, _resolve = stub_run_incremental
    last.return_value = None

    main(["incremental", "--local", str(tmp_path)])

    ri.assert_not_called()


def test_incremental_no_head_short_circuits(tmp_path: Path, stub_run_incremental) -> None:
    """Baseline resolves but HEAD does not (non-git dir / fresh repo): emit error."""
    ri, _last, head, _resolve = stub_run_incremental
    head.return_value = None

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc"])

    ri.assert_not_called()


def test_incremental_flags_default_to_none_in_parser() -> None:
    args = build_parser().parse_args(["incremental", "--local", "/tmp/repo"])
    assert args.base_ref is None
    assert args.target_ref is None
