import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure we can import from parent directory if run as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.config import PROJECTS_STATIC_ANALYSIS, PROJECTS_E2E, PROJECTS_SCALING
from evals.definitions.static_analysis import StaticAnalysisEval
from evals.definitions.end_to_end import EndToEndEval
from evals.definitions.scalability import ScalabilityEval

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run evaluations for CodeBoarding")
    parser.add_argument(
        "--type", choices=["static", "e2e", "scalability", "all"], default="all", help="Type of evaluation to run"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports"), help="Directory to save reports")
    args = parser.parse_args()

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Set up REPO_ROOT if not set, similar to original main()
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"

    evals_to_run = []

    if args.type in ["static", "all"]:
        evals_to_run.append(
            (StaticAnalysisEval("static-analysis", args.output_dir), PROJECTS_STATIC_ANALYSIS, ["--depth-level", "1"])
        )

    if args.type in ["e2e", "all"]:
        evals_to_run.append((EndToEndEval("end-to-end", args.output_dir), PROJECTS_E2E, []))

    if args.type in ["scalability", "all"]:
        evals_to_run.append((ScalabilityEval("scalability", args.output_dir), PROJECTS_SCALING, []))

    for eval_instance, projects, extra_args in evals_to_run:
        try:
            eval_instance.run(projects, extra_args)
        except Exception as e:
            logger.error(f"Evaluation {eval_instance.name} failed: {e}")
            # We continue to next eval if one fails
            continue


if __name__ == "__main__":
    main()
