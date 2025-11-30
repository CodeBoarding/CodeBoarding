import argparse
import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from agents.prompts import initialize_global_factory, PromptType, LLMType
from diagram_analysis.diagram_generator import DiagramGenerator
from logging_config import setup_logging
from vscode_constants import update_config

# Initialize the prompt factory for vscode_runnable to use unidirectional prompts
initialize_global_factory(LLMType.GEMINI_FLASH, PromptType.UNIDIRECTIONAL)


def cli_args():
    parser = argparse.ArgumentParser(description="Generate high-level diagrams for a local project.")
    parser.add_argument("--repo", type=str, help="Location of the local project (repository).")
    parser.add_argument("--project_name", type=str, help="Name of the project")
    parser.add_argument("--output_dir", type=str, default="./analysis", help="Output directory for the analysis files.")
    parser.add_argument(
        "--partial_updates_component",
        type=str,
        default=None,
        help="Component to update. If specified, only this component will be updated.",
    )
    parser.add_argument(
        "--partial_updates_analysis",
        type=str,
        default=None,
        help="The analysis for which the component will be updated",
    )

    parser.add_argument("--binary_location", type=str, help="Path to the binary to use for language servers.")
    return parser.parse_args()


args = cli_args()

setup_logging(log_dir=args.output_dir)
logger = logging.getLogger(__name__)


def partial_updates(component_to_update_name: str, analysis_to_update_name: str):
    repo_location = Path(args.repo)

    analysis_folder = Path(f"{args.output_dir}")
    if not analysis_folder.exists():
        analysis_folder.mkdir(parents=True, exist_ok=True)

    generator = DiagramGenerator(
        repo_location=repo_location,
        temp_folder=analysis_folder,
        repo_name=args.project_name,
        output_dir=analysis_folder,
        depth_level=1,
    )
    generator.pre_analysis()

    # Load the analysis for which we want to extend the component
    analysis_file = analysis_folder / f"{analysis_to_update_name}.json"
    with open(analysis_file, "r") as file:
        analysis = AnalysisInsights.model_validate_json(file.read())

    component_to_update = None
    for component in analysis.components:
        if component.name == component_to_update_name:
            logger.info(f"Updating analysis for component: {component.name}")
            component_to_update = component
            break
    # Generate analysis for the specified component in the output directory
    generator.process_component(component_to_update)


def full_analysis():
    repo_location = Path(args.repo)

    output_dir = Path(f"{args.output_dir}")
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    generator = DiagramGenerator(
        repo_location=repo_location,
        temp_folder=output_dir,
        repo_name=args.project_name,
        output_dir=output_dir,
        depth_level=1,
    )
    generator.generate_analysis()  # Generate the analysis files in the specified output directory


def main():
    assert args.binary_location is not None, "Please provide the --binary_location argument."

    # Update to run the commands properly
    update_config(Path(args.binary_location))

    if args.partial_updates_component and args.partial_updates_analysis:
        partial_updates(args.partial_updates_component, args.partial_updates_analysis)
    else:
        full_analysis()


if __name__ == "__main__":
    main()
