import argparse
import logging
from pathlib import Path

from diagram_generator import DiagramGenerator
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def args_parser():
    parser = argparse.ArgumentParser(description="Generate high-level diagrams for a local project.")
    parser.add_argument("--repo", type=str, help="Location of the local project (repository).")
    parser.add_argument("--project_name", type=str, help="Name of the project")
    parser.add_argument("--output_dir", type=str, default="./analysis", help="Output directory for the analysis files.")
    return parser


def main():
    parser = args_parser()
    args = parser.parse_args()

    repo_location = Path(args.repo)

    temp_folder = Path(f"{args.output_dir}/{args.project_name}")
    if not temp_folder.exists():
        temp_folder.mkdir(parents=True, exist_ok=True)

    generator = DiagramGenerator(repo_location=repo_location,
                                 temp_folder=temp_folder,
                                 repo_name=args.project_name,
                                 output_dir=temp_folder)
    generator.generate_analysis()  # This will generate the analysis files in the specified output directory


if __name__ == "__main__":
    main()
