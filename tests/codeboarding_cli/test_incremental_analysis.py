from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from main import build_parser, main


@pytest.fixture
def stub_run_incremental(tmp_path: Path):
    """Patch the chain so ``run_from_args`` reaches ``run_incremental`` without running it.

    Yields ``(run_incremental_mock, last_successful_commit_mock, get_current_commit_mock)``
    so individual tests can shape baseline / HEAD resolution.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"))
        rc = stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.RunContext"))
        rc.resolve.return_value.run_id = "rid"
        rc.resolve.return_value.log_path = "logs/run.log"
        last = stack.enter_context(
            patch("codeboarding_cli.commands.incremental_analysis.last_successful_commit", return_value="last-success")
        )
        head = stack.enter_context(
            patch("codeboarding_cli.commands.incremental_analysis.get_current_commit", return_value="current-head")
        )
        ri = stack.enter_context(
            patch(
                "codeboarding_cli.commands.incremental_analysis.run_incremental",
                return_value=tmp_path / "analysis.json",
            )
        )
        yield ri, last, head


def test_incremental_passes_base_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    """Explicit --base-ref wins over last_successful_commit."""
    ri, last, head = stub_run_incremental

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc123"])

    last.assert_not_called()
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "abc123"
    # No --target-ref: CLI resolves via get_current_commit.
    head.assert_called_once()
    assert kwargs["target_ref"] == "current-head"


def test_incremental_passes_target_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    """Explicit --target-ref wins over get_current_commit."""
    ri, _last, head = stub_run_incremental

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc", "--target-ref", "HEAD"])

    head.assert_not_called()
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "abc"
    assert kwargs["target_ref"] == "HEAD"


def test_incremental_no_flags_resolves_from_metadata_and_head(tmp_path: Path, stub_run_incremental) -> None:
    """No flags: CLI resolves base from last_successful_commit and target from current HEAD."""
    ri, last, head = stub_run_incremental

    main(["incremental", "--local", str(tmp_path)])

    last.assert_called_once()
    head.assert_called_once()
    kwargs = ri.call_args.kwargs
    assert kwargs["base_ref"] == "last-success"
    assert kwargs["target_ref"] == "current-head"


def test_incremental_no_baseline_short_circuits(tmp_path: Path, stub_run_incremental) -> None:
    """No --base-ref and no last_successful_commit: emit error, never call run_incremental."""
    ri, last, _head = stub_run_incremental
    last.return_value = None

    main(["incremental", "--local", str(tmp_path)])

    ri.assert_not_called()


def test_incremental_no_head_short_circuits(tmp_path: Path, stub_run_incremental) -> None:
    """Baseline resolves but HEAD does not (non-git dir / fresh repo): emit error."""
    ri, _last, head = stub_run_incremental
    head.return_value = None

    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc"])

    ri.assert_not_called()


def test_incremental_flags_default_to_none_in_parser() -> None:
    args = build_parser().parse_args(["incremental", "--local", "/tmp/repo"])
    assert args.base_ref is None
    assert args.target_ref is None
