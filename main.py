"""
Unified entry point for CodeBoarding documentation generation.
Supports both local and remote repository analysis.
"""

import argparse
import logging
import os
import shutil
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from agents.agent_responses import AnalysisInsights
from agents.prompts import initialize_global_factory, PromptType, LLMType
from diagram_analysis import DiagramGenerator
from logging_config import setup_logging
from output_generators.markdown import generate_markdown_file
from repo_utils import clone_repository, get_branch, get_repo_name, store_token, upload_onboarding_materials
from utils import caching_enabled, create_temp_repo_folder, remove_temp_repo_folder
from vscode_constants import update_config

logger = logging.getLogger(__name__)


def validate_env_vars():
    """Validate that required API keys and environment variables are set."""
    api_provider_keys = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "OLLAMA_BASE_URL",
        "CEREBRAS_API_KEY",
    ]
    api_env_keys = [(key, os.getenv(key)) for key in api_provider_keys if os.getenv(key) is not None]

    if len(api_env_keys) == 0:
        logger.error(f"API key not set, set one of the following: {api_provider_keys}")
        exit(1)
    elif len(api_env_keys) > 1:
        logger.error(f"Detected multiple API keys set ({api_env_keys}), set ONE of the following: {api_provider_keys}")
        exit(2)


def setup_environment(project_root: Path | None = None):
    """
    Set up environment variables with defaults if not already set.

    Args:
        project_root: Optional project root path. If not provided, uses current directory.
    """
    if project_root is None:
        project_root = Path.cwd()

    # Set defaults for environment variables that can be deduced
    if not os.getenv("REPO_ROOT"):
        repo_root = project_root / "repos"
        os.environ["REPO_ROOT"] = str(repo_root)
        logger.info(f"REPO_ROOT not set, using default: {repo_root}")

    if not os.getenv("ROOT_RESULT"):
        root_result = project_root / "results"
        os.environ["ROOT_RESULT"] = str(root_result)
        logger.info(f"ROOT_RESULT not set, using default: {root_result}")

    if not os.getenv("STATIC_ANALYSIS_CONFIG"):
        static_config = project_root / "static_analysis_config.yml"
        os.environ["STATIC_ANALYSIS_CONFIG"] = str(static_config)
        logger.info(f"STATIC_ANALYSIS_CONFIG not set, using default: {static_config}")

    if not os.getenv("PROJECT_ROOT"):
        os.environ["PROJECT_ROOT"] = str(project_root)
        logger.info(f"PROJECT_ROOT not set, using default: {project_root}")

    if not os.getenv("DIAGRAM_DEPTH_LEVEL"):
        os.environ["DIAGRAM_DEPTH_LEVEL"] = "1"
        logger.info(f"DIAGRAM_DEPTH_LEVEL not set, using default: 1")


def onboarding_materials_exist(project_name: str) -> bool:
    """Check if onboarding materials already exist in the generated repository."""
    generated_repo_url = f"https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main/{project_name}"
    response = requests.get(generated_repo_url)
    if response.status_code == 200:
        logger.info(f"Repository has already been generated, please check {generated_repo_url}")
        return True
    return False


def generate_docs(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    depth_level: int = 1,
    repo_url: str | None = None,
    demo_mode: bool = False,
):
    """
    Generate documentation for a repository.

    Args:
        repo_name: Name of the repository
        repo_path: Path to the repository
        output_dir: Directory for output files
        depth_level: Depth level for diagram generation
        repo_url: Optional URL of the repository (for linking in docs)
        demo_mode: Whether to run in demo mode
    """
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
    )
    analysis_files = generator.generate_analysis()

    # Generate markdown from analysis files
    for file in analysis_files:
        with open(file, "r") as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logger.info(f"Generated analysis file: {file}")
            fname = Path(file).name.split(".json")[0]
            if fname.endswith("analysis"):
                fname = "on_boarding"

            # Get branch for linking if repo_url is provided
            repo_ref = None
            if repo_url:
                target_branch = get_branch(repo_path)
                repo_ref = f"{repo_url}/blob/{target_branch}/"

            generate_markdown_file(
                fname,
                analysis,
                repo_name,
                repo_ref=repo_ref,
                linked_files=analysis_files,
                temp_dir=output_dir,
                demo=demo_mode,
            )


def partial_update(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    component_name: str,
    analysis_name: str,
    depth_level: int = 1,
):
    """
    Update a specific component in an existing analysis.

    Args:
        repo_path: Path to the repository
        output_dir: Directory containing analysis files
        project_name: Name of the project
        component_name: Name of the component to update
        analysis_name: Name of the analysis file to update
        depth_level: Depth level for diagram generation
    """
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=project_name,
        output_dir=output_dir,
        depth_level=depth_level,
    )
    generator.pre_analysis()

    # Load the analysis for which we want to extend the component
    analysis_file = output_dir / f"{analysis_name}.json"
    with open(analysis_file, "r") as file:
        analysis = AnalysisInsights.model_validate_json(file.read())

    # Find and update the component
    component_to_update = None
    for component in analysis.components:
        if component.name == component_name:
            logger.info(f"Updating analysis for component: {component.name}")
            component_to_update = component
            break

    if component_to_update is None:
        logger.error(f"Component '{component_name}' not found in analysis '{analysis_name}'")
        return

    generator.process_component(component_to_update)


def process_remote_repository(
    repo_url: str,
    output_dir: Path | None = None,
    depth_level: int = 1,
    upload: bool = False,
    cache_check: bool = True,
):
    """
    Process a remote repository by cloning and generating documentation.

    Args:
        repo_url: URL of the remote repository
        output_dir: Optional output directory for generated files
        depth_level: Depth level for diagram generation
        upload: Whether to upload onboarding materials
        cache_check: Whether to check if materials already exist
    """
    repo_root = Path(os.getenv("REPO_ROOT", "repos"))
    root_result = os.getenv("ROOT_RESULT", "results")

    repo_name = get_repo_name(repo_url)

    # Check cache if enabled
    if cache_check and caching_enabled() and onboarding_materials_exist(repo_name):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    # Clone repository
    repo_name = clone_repository(repo_url, repo_root)
    repo_path = repo_root / repo_name

    # Create temp folder for output
    temp_folder = create_temp_repo_folder()

    try:
        # Generate documentation
        generate_docs(
            repo_name=repo_name,
            repo_path=repo_path,
            output_dir=temp_folder,
            depth_level=depth_level,
            repo_url=repo_url,
            demo_mode=True,
        )

        # Copy files to output directory if specified
        if output_dir:
            copy_files(temp_folder, output_dir)

        # Upload if requested
        if upload and os.path.exists(root_result):
            upload_onboarding_materials(repo_name, temp_folder, root_result)
        elif upload:
            logger.warning(f"ROOT_RESULT directory '{root_result}' does not exist. Skipping upload.")
    finally:
        remove_temp_repo_folder(temp_folder)


def process_local_repository(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    depth_level: int = 1,
    component_name: str | None = None,
    analysis_name: str | None = None,
):
    """
    Process a local repository and generate documentation.

    Args:
        repo_path: Path to the local repository
        output_dir: Directory for output files
        project_name: Name of the project
        depth_level: Depth level for diagram generation
        component_name: Optional component name for partial updates
        analysis_name: Optional analysis name for partial updates
    """
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    # Handle partial updates
    if component_name and analysis_name:
        partial_update(
            repo_path=repo_path,
            output_dir=output_dir,
            project_name=project_name,
            component_name=component_name,
            analysis_name=analysis_name,
            depth_level=depth_level,
        )
    else:
        # Full analysis
        generate_docs(
            repo_name=project_name,
            repo_path=repo_path,
            output_dir=output_dir,
            depth_level=depth_level,
            demo_mode=False,
        )


def copy_files(temp_folder: Path, output_dir: Path):
    """Copy all markdown and JSON files from temp folder to output directory."""
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created output directory: {output_dir}")

    # Copy markdown files
    markdown_files = list(temp_folder.glob("*.md"))
    # Copy JSON files
    json_files = list(temp_folder.glob("*.json"))

    all_files = markdown_files + json_files

    if not all_files:
        logger.warning(f"No markdown or JSON files found in {temp_folder}")
        return

    for file in all_files:
        dest_file = output_dir / file.name
        shutil.copy2(file, dest_file)
        logger.info(f"Copied {file.name} to {dest_file}")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate onboarding documentation for Git repositories (local or remote)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Remote repositories
  python main.py https://github.com/user/repo1
  python main.py https://github.com/user/repo1 --output-dir ./docs
  python main.py https://github.com/user/repo1 https://github.com/user/repo2 --output-dir ./output

  # Local repository
  python main.py --local /path/to/repo --project-name MyProject --output-dir ./analysis

  # Partial update
  python main.py --local /path/to/repo --project-name MyProject --output-dir ./analysis \\
                 --partial-component ComponentName --partial-analysis analysis_name

  # Use custom binary location (for VSCode integration)
  python main.py --local /path/to/repo --project-name MyProject --binary-location /path/to/binaries
        """,
    )

    # Repository specification (mutually exclusive groups)
    repo_group = parser.add_mutually_exclusive_group(required=True)
    repo_group.add_argument(
        "repositories", nargs="*", help="One or more Git repository URLs to generate documentation for"
    )
    repo_group.add_argument("--local", type=Path, help="Path to a local repository")

    # Output configuration
    parser.add_argument("--output-dir", type=Path, help="Directory to output generated files to")

    # Local repository specific options
    parser.add_argument("--project-name", type=str, help="Name of the project (required for local repositories)")

    # Partial update options
    parser.add_argument("--partial-component", type=str, help="Component to update (for partial updates only)")
    parser.add_argument("--partial-analysis", type=str, help="Analysis file to update (for partial updates only)")

    # Advanced options
    parser.add_argument(
        "--binary-location", type=Path, help="Path to the binary directory for language servers (VSCode integration)"
    )
    parser.add_argument("--depth-level", type=int, default=1, help="Depth level for diagram generation (default: 1)")
    parser.add_argument(
        "--prompt-type",
        choices=["bidirectional", "unidirectional"],
        default=None,
        help="Prompt type to use (default: bidirectional for remote, unidirectional for local)",
    )
    parser.add_argument(
        "--no-upload", action="store_true", help="Skip uploading onboarding materials (remote repos only)"
    )
    parser.add_argument(
        "--no-cache-check", action="store_true", help="Skip checking if materials already exist (remote repos only)"
    )
    parser.add_argument("--project-root", type=Path, help="Project root directory (default: current directory)")

    return parser.parse_args()


def main():
    """Main entry point for the unified CodeBoarding CLI."""
    args = parse_args()

    # Load environment from .env file if it exists
    load_dotenv()

    # Setup environment with defaults
    setup_environment(args.project_root)

    # Determine mode based on arguments
    is_local = args.local is not None

    # Validate local repository arguments
    if is_local and not args.project_name:
        parser = argparse.ArgumentParser()
        parser.error("--project-name is required when using --local")

    # Validate partial update arguments
    if (args.partial_component or args.partial_analysis) and not is_local:
        parser = argparse.ArgumentParser()
        parser.error("Partial updates (--partial-component, --partial-analysis) only work with local repositories")

    if args.partial_component and not args.partial_analysis:
        parser = argparse.ArgumentParser()
        parser.error("--partial-analysis is required when using --partial-component")

    if args.partial_analysis and not args.partial_component:
        parser = argparse.ArgumentParser()
        parser.error("--partial-component is required when using --partial-analysis")

    # Determine prompt type: bidirectional for remote (github repo link), unidirectional for local
    if args.prompt_type:
        prompt_type = PromptType.BIDIRECTIONAL if args.prompt_type == "bidirectional" else PromptType.UNIDIRECTIONAL
    else:
        prompt_type = PromptType.UNIDIRECTIONAL if is_local else PromptType.BIDIRECTIONAL

    # Initialize prompt factory
    initialize_global_factory(LLMType.GEMINI_FLASH, prompt_type)

    # Validate environment variables (only for remote repos that need API access)
    if not is_local:
        validate_env_vars()

    # Handle binary location for VSCode integration
    if args.binary_location:
        update_config(args.binary_location)

    # Setup logging
    log_dir = str(args.output_dir) if args.output_dir else None
    setup_logging(log_dir=log_dir)
    logger.info("Starting CodeBoarding documentation generation...")

    # Process repositories
    if is_local:
        # Local repository processing
        process_local_repository(
            repo_path=args.local,
            output_dir=args.output_dir or Path("./analysis"),
            project_name=args.project_name,
            depth_level=args.depth_level,
            component_name=args.partial_component,
            analysis_name=args.partial_analysis,
        )
        logger.info(f"Documentation generated successfully in {args.output_dir or './analysis'}")
    else:
        # Remote repositories processing
        if args.repositories:
            # Store GitHub token if needed
            if not args.no_upload:
                try:
                    store_token()
                except Exception as e:
                    logger.warning(f"Could not store GitHub token: {e}")

            # Process each repository
            for repo in tqdm(args.repositories, desc="Generating docs for repos"):
                try:
                    process_remote_repository(
                        repo_url=repo,
                        output_dir=args.output_dir,
                        depth_level=args.depth_level,
                        upload=not args.no_upload,
                        cache_check=not args.no_cache_check,
                    )
                except Exception as e:
                    logger.error(f"Failed to process repository {repo}: {e}")
                    continue

            logger.info("All repositories processed successfully!")
        else:
            logger.error("No repositories specified")


if __name__ == "__main__":
    main()
