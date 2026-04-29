from pathlib import Path
from unittest.mock import MagicMock, patch

from codeboarding_workflows.analysis import run_incremental


def test_run_incremental_forwards_static_analyzer_to_generator(tmp_path: Path) -> None:
    """Warm-LSP injection: ``static_analyzer`` must reach ``DiagramGenerator.__init__``.

    Why: the wrapper hands a long-lived StaticAnalyzer with warm LSP servers
    in via this kwarg; if it's silently dropped on the workflow boundary,
    incremental analysis cold-starts a new analyzer instead.
    """
    sentinel_analyzer = MagicMock(name="static_analyzer")

    with (
        patch("codeboarding_workflows.analysis.DiagramGenerator") as gen_cls,
        patch("codeboarding_workflows.analysis.run_incremental_pipeline", return_value=MagicMock()),
    ):
        run_incremental(
            repo_path=tmp_path,
            output_dir=tmp_path / "out",
            project_name="proj",
            run_id="rid",
            log_path="logs/run.log",
            base_ref="abc",
            target_ref="",
            static_analyzer=sentinel_analyzer,
        )

    gen_cls.assert_called_once()
    assert gen_cls.call_args.kwargs["static_analyzer"] is sentinel_analyzer
