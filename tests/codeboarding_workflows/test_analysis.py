from pathlib import Path
from unittest.mock import patch

import pytest

from codeboarding_workflows.analysis import BaselineUnavailableError, run_partial
from diagram_analysis.run_context import RunContext, RunPaths


def test_run_partial_no_baseline_raises(tmp_path: Path) -> None:
    with patch("codeboarding_workflows.analysis.load_analysis_metadata", return_value=None):
        with pytest.raises(BaselineUnavailableError, match="No baseline"):
            run_partial(
                RunPaths(repo_path=tmp_path, output_dir=tmp_path / "out", project_name="proj"),
                RunContext(run_id="rid", log_path="logs/run.log", repo_dir=tmp_path),
                component_id="comp",
            )
