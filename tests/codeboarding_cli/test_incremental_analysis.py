from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from main import build_parser, main


@pytest.fixture
def stub_run_incremental(tmp_path: Path):
    """Patch the chain so ``run_from_args`` reaches ``run_incremental`` without running it.

    Detection is fully git-free and internal: the CLI passes only paths and run
    context — no changed-file set, no git refs, no source hash.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"))
        rc = stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.RunContext"))
        rc.resolve.return_value.run_id = "rid"
        rc.resolve.return_value.log_path = "logs/run.log"
        ri = stack.enter_context(
            patch(
                "codeboarding_cli.commands.incremental_analysis.run_incremental",
                return_value=tmp_path / "analysis.json",
            )
        )
        yield ri


def test_incremental_calls_run_incremental_with_paths_only(tmp_path: Path, stub_run_incremental) -> None:
    ri = stub_run_incremental

    main(["incremental", "--local", str(tmp_path)])

    ri.assert_called_once()
    kwargs = ri.call_args.kwargs
    assert kwargs["repo_path"] == tmp_path
    assert kwargs["output_dir"] == tmp_path / ".codeboarding"
    # No change-detection inputs cross the boundary — Core detects internally.
    assert "base_ref" not in kwargs
    assert "target_ref" not in kwargs
    assert "changes" not in kwargs
    assert "source_sha" not in kwargs


def test_incremental_no_git_ref_flags_in_parser() -> None:
    """The incremental subcommand exposes no git-ref flags — detection is internal."""
    parser = build_parser()
    args = parser.parse_args(["incremental", "--local", "/tmp/repo"])
    assert not hasattr(args, "base_ref")
    assert not hasattr(args, "target_ref")
