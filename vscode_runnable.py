import argparse
import logging
from pathlib import Path

from graph_generator import GraphGenerator
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def args_parser():
    parser = argparse.ArgumentParser(description="Run a Python script with arguments.")
    parser.add_argument("--repo", type=str, help="The Python script to run.")
    parser.add_argument("--project_name", type=str, help="Arguments to pass to the script.")
    parser.add_argument("--output_dir", type=str, default="./analysis", help="Output directory for the analysis files.")
    return parser


def main():
    parser = args_parser()
    args = parser.parse_args()

    repo_location = Path(args.repo)

    temp_folder = Path(f"{args.output_dir}/{args.project_name}")
    if not temp_folder.exists():
        temp_folder.mkdir(parents=True, exist_ok=True)

    generator = GraphGenerator(repo_location=repo_location,
                               temp_folder=temp_folder,
                               repo_name=args.project_name,
                               output_dir=temp_folder)
    analysis_files = generator.generate_analysis()


if __name__ == "__main__":
    main()
