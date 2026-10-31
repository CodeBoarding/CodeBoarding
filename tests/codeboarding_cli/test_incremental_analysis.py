import json
import logging
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.exceptions import ScopeSemanticsError
from diagram_analysis.io_utils import save_analysis, write_fingerprint
from codeboarding_cli.main import main
from static_analyzer.clustering.names.spec import SPEC_VERSION
from static_analyzer.scanner import ProjectScanner


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


@pytest.fixture
def baseline_from_an_older_engine(tmp_path: Path):
    """A checkout and a seeded state directory whose pkl an older engine wrote.

    Only the LLM work and the tokei scan are stubbed, so ``run_from_args`` meets the real refusal.
    """

    def seed(state: str) -> tuple[Path, Path]:
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "a.py").write_text("def a():\n    pass\n")
        output_dir = tmp_path / "_temp" / state
        output_dir.mkdir(parents=True)
        save_analysis(
            AnalysisInsights(
                description="root",
                components=[Component(name="A", description="", key_entities=[], component_id="1")],
                components_relations=[],
            ),
            output_dir,
            checkout,
            source_tree_hash="abc",
            depth_cap=4,
            tree_spec={
                "version": SPEC_VERSION,
                "grouper": "kinship",
                "machinery": [],
                "scopes": {"root": {"rules": []}},
            },
        )
        write_fingerprint(output_dir, {"a.py": "0"})
        (output_dir / "static_analysis.pkl").write_bytes(b"not-a-real-pickle")
        (output_dir / "static_analysis.sha").write_text("v1\ndeadbeef\n")
        return checkout, output_dir

    with ExitStack() as stack:
        stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"))
        rc = stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.RunContext"))
        rc.resolve.return_value.run_id = "rid"
        rc.resolve.return_value.log_path = "logs/run.log"
        stack.enter_context(patch.object(ProjectScanner, "scan", return_value=[]))
        generate = stack.enter_context(patch.object(DiagramGenerator, "generate_analysis", autospec=True))
        yield seed, generate


@pytest.mark.parametrize("state", ["codeboarding-sync/analysis", "codeboarding-review/base-state"])
def test_a_baseline_from_another_engine_is_rebuilt_in_full(
    baseline_from_an_older_engine, state: str, capsys, caplog
) -> None:
    """Why: every engine release reaches CI unasked, and these are the two places the Action seeds a baseline."""
    seed, generate = baseline_from_an_older_engine
    checkout, output_dir = seed(state)
    generate.return_value = output_dir / "analysis.json"

    with caplog.at_level(logging.WARNING):
        main(["incremental", "--local", str(checkout), "--output-dir", str(output_dir)])

    assert json.loads(capsys.readouterr().out) == {
        "analysis_path": str(output_dir / "analysis.json"),
        "mode": "full",
        "requiresFullAnalysis": False,
    }
    generator = generate.call_args.args[0]
    generate.assert_called_once()
    assert generator.force_full_analysis
    assert generator.depth_cap == 4
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1
    assert "different engine version" in warnings[0]


def test_a_failed_full_run_does_not_ask_for_another(baseline_from_an_older_engine, capsys) -> None:
    """Why: ``requiresFullAnalysis`` would have the Action pay for the same full run a second time."""
    seed, generate = baseline_from_an_older_engine
    checkout, output_dir = seed("codeboarding-sync/analysis")
    generate.side_effect = RuntimeError("the full run failed")

    with pytest.raises(RuntimeError, match="the full run failed"):
        main(["incremental", "--local", str(checkout), "--output-dir", str(output_dir)])

    assert "requiresFullAnalysis" not in capsys.readouterr().out
