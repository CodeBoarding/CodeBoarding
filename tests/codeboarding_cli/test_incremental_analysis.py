from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from main import build_parser, main


@pytest.fixture
def stub_run_incremental(tmp_path: Path):
    """Patch the chain so ``run_from_args`` reaches ``run_incremental`` without running it."""
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


def test_incremental_passes_base_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc123"])

    kwargs = stub_run_incremental.call_args.kwargs
    assert kwargs["base_ref"] == "abc123"
    assert kwargs["target_ref"] is None


def test_incremental_passes_target_ref_through(tmp_path: Path, stub_run_incremental) -> None:
    main(["incremental", "--local", str(tmp_path), "--base-ref", "abc", "--target-ref", "HEAD"])

    kwargs = stub_run_incremental.call_args.kwargs
    assert kwargs["base_ref"] == "abc"
    assert kwargs["target_ref"] == "HEAD"


def test_incremental_no_flags_defaults_to_none(tmp_path: Path, stub_run_incremental) -> None:
    main(["incremental", "--local", str(tmp_path)])

    kwargs = stub_run_incremental.call_args.kwargs
    assert kwargs["base_ref"] is None
    assert kwargs["target_ref"] is None


def test_incremental_flags_default_to_none_in_parser() -> None:
    args = build_parser().parse_args(["incremental", "--local", "/tmp/repo"])
    assert args.base_ref is None
    assert args.target_ref is None
