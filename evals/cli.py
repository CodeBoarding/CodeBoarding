import argparse
import logging
import os
import sys
from typing import Any, Tuple
from pathlib import Path

from dotenv import load_dotenv

# Ensure we can import from parent directory if run as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.tasks.static_analysis import StaticAnalysisEval
from evals.tasks.static_analysis.config import PROJECTS as PROJECTS_STATIC_ANALYSIS
from evals.tasks.end_to_end import EndToEndEval
from evals.tasks.end_to_end.config import PROJECTS as PROJECTS_E2E
from evals.tasks.scalability import ScalabilityEval
from evals.tasks.scalability.config import PROJECTS as PROJECTS_SCALING
from evals.tasks.accuracy import AccuracyEval
from evals.tasks.accuracy.config import PROJECTS as PROJECTS_ACCURACY
from evals.schemas import ProjectSpec

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Run evaluations for CodeBoarding")
    parser.add_argument(
        "--type",
        choices=["static", "e2e", "scalability", "accuracy", "all"],
        default="all",
        help="Type of evaluation to run",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports"), help="Directory to save reports")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Skip pipeline execution and generate reports from existing artifacts",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        metavar="N",
        help="Run N projects concurrently (default: sequential)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Set up REPO_ROOT if not set, similar to original main()
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"

    evals_to_run: list[Tuple[Any, list[ProjectSpec], list[str]]] = []

    if args.type in ["static", "all"]:
        evals_to_run.append(
            (StaticAnalysisEval("static-analysis", args.output_dir), PROJECTS_STATIC_ANALYSIS, ["--static-only"])
        )

    if args.type in ["e2e", "all"]:
        evals_to_run.append((EndToEndEval("end-to-end", args.output_dir), PROJECTS_E2E, []))

    if args.type in ["scalability", "all"]:
        evals_to_run.append((ScalabilityEval("scalability", args.output_dir), PROJECTS_SCALING, []))

    if args.type in ["accuracy", "all"]:
        evals_to_run.append((AccuracyEval("accuracy", args.output_dir), PROJECTS_ACCURACY, []))

    for eval_instance, projects, extra_args in evals_to_run:
        try:
            eval_instance.run(
                projects,
                extra_args,
                report_only=args.report_only,
                max_concurrency=args.parallel,
            )
        except Exception as e:
            logger.error(f"Evaluation {eval_instance.name} failed: {e}")
            # We continue to next eval if one fails
            continue


if __name__ == "__main__":
    main()
