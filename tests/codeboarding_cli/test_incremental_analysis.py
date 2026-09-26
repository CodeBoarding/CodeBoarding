from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from diagram_analysis.exceptions import ScopeSemanticsError
from main import main


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
    run_paths = ri.call_args.args[0]
    assert run_paths.repo_path == tmp_path
    assert run_paths.output_dir == tmp_path / ".codeboarding"


def test_incremental_llm_failure_crashes_instead_of_asking_for_a_full_run(
    tmp_path: Path, stub_run_incremental, capsys
) -> None:
    stub_run_incremental.side_effect = ScopeSemanticsError("root", telemetry_properties={})

    with pytest.raises(ScopeSemanticsError):
        main(["incremental", "--local", str(tmp_path)])

    assert "requiresFullAnalysis" not in capsys.readouterr().out
