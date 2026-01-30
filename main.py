import argparse
import logging
import os
import shutil
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from agents.agent_responses import AnalysisInsights

from diagram_analysis import DiagramGenerator
from static_analyzer import StaticAnalyzer
from static_analyzer.cluster_helpers import build_all_cluster_results
from logging_config import setup_logging
from output_generators.markdown import generate_markdown_file
from repo_utils import clone_repository, get_branch, get_repo_name, store_token, upload_onboarding_materials
from repo_utils.ignore import initialize_codeboardingignore
from utils import caching_enabled, create_temp_repo_folder, monitoring_enabled, remove_temp_repo_folder
from monitoring import monitor_execution
from monitoring.paths import generate_run_id, get_monitoring_run_dir
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
        "VERCEL_API_KEY",
    ]
    api_env_keys = [(key, os.getenv(key)) for key in api_provider_keys if os.getenv(key) is not None]

    if len(api_env_keys) == 0:
        logger.error(f"API key not set, set one of the following: {api_provider_keys}")
        exit(1)
    elif len(api_env_keys) > 1:
        logger.error(f"Detected multiple API keys set ({api_env_keys}), set ONE of the following: {api_provider_keys}")
        exit(2)


def onboarding_materials_exist(project_name: str) -> bool:
    generated_repo_url = f"https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main/{project_name}"
    response = requests.get(generated_repo_url)
    if response.status_code == 200:
        logger.info(f"Repository has already been generated, please check {generated_repo_url}")
        return True
    return False


def generate_analysis(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    depth_level: int = 1,
    run_id: str | None = None,
    monitoring_enabled: bool = False,
    force_full: bool = False,
) -> list[str]:
    """
    Generate analysis for a repository.

    Automatically detects if cached analysis exists and uses iterative updates
    when possible for better performance:
    - SMALL changes: File reassignments only (instant)
    - MEDIUM changes: Update affected components (seconds)
    - BIG changes: Full re-analysis (minutes)

    Args:
        force_full: If True, ignore cache and perform full analysis
    """
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        monitoring_enabled=monitoring_enabled,
    )

    # Check if we have cached analysis for iterative update
    cache_dir = output_dir / ".analysis_cache"
    has_cache = cache_dir.exists() and any(cache_dir.glob("*_L*.json"))

    if force_full:
        logger.info("--full flag set, performing full analysis (ignoring cache)")
        # Clear the analysis cache to ensure fresh start
        if cache_dir.exists():
            import shutil

            shutil.rmtree(cache_dir)
            logger.info(f"Cleared analysis cache: {cache_dir}")
        return generator.generate_analysis()
    elif has_cache:
        logger.info("Found cached analysis, using iterative update")
        return _generate_analysis_iterative(generator, repo_path, output_dir)
    else:
        logger.info("No cached analysis found, performing full analysis")
        return generator.generate_analysis()


def _generate_analysis_iterative(
    generator: DiagramGenerator,
    repo_path: Path,
    output_dir: Path,
) -> list[str]:
    """
    Perform iterative analysis update using change classification.

    This function:
    1. Runs static analysis with cluster change detection
    2. Determines change magnitude (SMALL/MEDIUM/BIG)
    3. Routes to appropriate update strategy
    """
    from repo_utils import get_git_commit_hash
    from static_analyzer.cluster_change_analyzer import ChangeClassification

    # Run static analysis with cluster change detection
    logger.info("Running static analysis with cluster change detection")
    static_analyzer = StaticAnalyzer(repo_path)
    cache_dir = output_dir / ".analysis_cache"

    result = static_analyzer.analyze_with_cluster_changes(cache_dir=cache_dir)

    classification = result["change_classification"]
    cluster_change = result.get("cluster_change_result")

    # Build cluster results from static analysis
    analysis_result = result["analysis_result"]
    cluster_results = build_all_cluster_results(analysis_result)

    # Get current commit
    current_commit = get_git_commit_hash(repo_path)

    logger.info(f"Change classification: {classification.value}")
    if cluster_change:
        logger.info(
            f"Cluster changes: {len(cluster_change.matched_clusters)} matched, "
            f"{len(cluster_change.new_clusters)} new, "
            f"{len(cluster_change.removed_clusters)} removed"
        )

    # Route to appropriate update strategy
    return generator.generate_analysis_iterative(
        change_classification=classification,
        cluster_change_result=cluster_change,
        cluster_results=cluster_results,
        current_commit=current_commit,
    )


def generate_markdown_docs(
    repo_name: str,
    repo_path: Path,
    repo_url: str,
    analysis_files: list[Path],
    output_dir: Path,
    demo_mode: bool = False,
):
    target_branch = get_branch(repo_path)
    repo_ref = f"{repo_url}/blob/{target_branch}/"

    for file in analysis_files:
        with open(file, "r") as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logger.info(f"Generating markdown for analysis file: {file}")
            fname = Path(file).name.split(".json")[0]
            if fname.endswith("analysis"):
                fname = "on_boarding"

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
    """
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
    try:
        with open(analysis_file, "r") as file:
            analysis = AnalysisInsights.model_validate_json(file.read())
    except FileNotFoundError:
        logger.error(f"Analysis file '{analysis_file}' not found. Please ensure the file exists.")
        return
    except Exception as e:
        logger.error(f"Failed to load analysis file '{analysis_file}': {e}")
        return

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


def generate_docs_remote(
    repo_url: str,
    temp_repo_folder: Path,
    local_dev: bool = False,
    run_id: str | None = None,
    monitoring_enabled: bool = False,
):
    """
    Clone a git repo and generate documentation (backward compatibility wrapper used by local_app).
    """
    process_remote_repository(
        repo_url=repo_url,
        output_dir=temp_repo_folder,
        depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")),
        upload=not local_dev,  # Only upload if not in local dev mode
        cache_check=True,
        run_id=run_id,
        monitoring_enabled=monitoring_enabled,
    )


def process_remote_repository(
    repo_url: str,
    output_dir: Path | None = None,
    depth_level: int = 1,
    upload: bool = False,
    cache_check: bool = True,
    run_id: str | None = None,
    monitoring_enabled: bool = False,
    force_full: bool = False,
):
    """
    Process a remote repository by cloning and generating documentation.
    """
    repo_root = Path(os.getenv("REPO_ROOT", "repos"))
    root_result = os.getenv("ROOT_RESULT", "results")

    repo_name = get_repo_name(repo_url)

    # Check cache if enabled (only for remote upload cache, not analysis cache)
    if cache_check and caching_enabled() and onboarding_materials_exist(repo_name):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    # Clone repository
    repo_name = clone_repository(repo_url, repo_root)
    repo_path = repo_root / repo_name

    temp_folder = create_temp_repo_folder()

    try:
        analysis_files = generate_analysis(
            repo_name=repo_name,
            repo_path=repo_path,
            output_dir=temp_folder,
            depth_level=depth_level,
            run_id=run_id,
            monitoring_enabled=monitoring_enabled,
            force_full=force_full,
        )

        # Generate markdown documentation for remote repo
        generate_markdown_docs(
            repo_name=repo_name,
            repo_path=repo_path,
            repo_url=repo_url,
            analysis_files=analysis_files,
            output_dir=temp_folder,
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
    monitoring_enabled: bool = False,
    force_full: bool = False,
):
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
        # Full analysis (local repo - no markdown generation)
        generate_analysis(
            repo_name=project_name,
            repo_path=repo_path,
            output_dir=output_dir,
            depth_level=depth_level,
            monitoring_enabled=monitoring_enabled,
            force_full=force_full,
        )


def copy_files(temp_folder: Path, output_dir: Path):
    """Copy all markdown and JSON files from temp folder to output directory."""
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


def validate_arguments(args, parser, is_local: bool):
    # Ensure mutual exclusivity between remote and local runs
    has_remote_repos = bool(getattr(args, "repositories", None))
    has_local_repo = args.local is not None

    if has_remote_repos == has_local_repo:
        parser.error("Provide either one or more remote repositories or --local, but not both.")

    # Validate local repository arguments
    if is_local and not args.project_name:
        parser.error("--project-name is required when using --local")

    # Validate partial update arguments
    if (args.partial_component or args.partial_analysis) and not is_local:
        parser.error("Partial updates (--partial-component, --partial-analysis) only work with local repositories")

    if args.partial_component and not args.partial_analysis:
        parser.error("--partial-analysis is required when using --partial-component")

    if args.partial_analysis and not args.partial_component:
        parser.error("--partial-component is required when using --partial-analysis")


def define_cli_arguments(parser: argparse.ArgumentParser):
    """
    Adds all command-line arguments and groups to the ArgumentParser.
    """
    parser.add_argument("repositories", nargs="*", help="One or more Git repository URLs to generate documentation for")
    parser.add_argument("--local", type=Path, help="Path to a local repository")

    # Output configuration
    parser.add_argument("--output-dir", type=Path, help="Directory to output generated files to")

    # Local repository specific options
    parser.add_argument("--project-name", type=str, help="Name of the project (required for local repositories)")

    # Partial update options
    parser.add_argument("--partial-component", type=str, help="Component to update (for partial updates only)")
    parser.add_argument("--partial-analysis", type=str, help="Analysis file to update (for partial updates only)")

    # Advanced options
    parser.add_argument(
        "--load-env-variables",
        action="store_true",
        help="Load the .env file for environment variables",
    )
    parser.add_argument("--binary-location", type=Path, help="Path to the binary directory for language servers")
    parser.add_argument("--depth-level", type=int, default=1, help="Depth level for diagram generation (default: 1)")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload onboarding materials to GeneratedOnBoardings repo (remote repos only)",
    )
    parser.add_argument(
        "--no-cache-check", action="store_true", help="Skip checking if materials already exist (remote repos only)"
    )
    parser.add_argument("--project-root", type=Path, help="Project root directory (default: current directory)")
    parser.add_argument("--enable-monitoring", action="store_true", help="Enable monitoring")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force full analysis, ignoring any cached analysis (useful for regenerating from scratch)",
    )


def main():
    """Main entry point for the unified CodeBoarding CLI."""
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

  # Force full analysis (ignore cache)
  python main.py --local /path/to/repo --project-name MyProject --output-dir ./analysis --full

  # Use custom binary location
  python main.py --local /path/to/repo --project-name MyProject --binary-location /path/to/binaries
        """,
    )
    define_cli_arguments(parser)

    args = parser.parse_args()

    # Validate interdependent arguments
    is_local = args.local is not None
    validate_arguments(args, parser, is_local)

    # Setup logging first, before any operations that might log
    log_dir: Path | None = args.output_dir if args.output_dir else None
    setup_logging(log_dir=log_dir)
    logger.info("Starting CodeBoarding documentation generation...")

    # Load environment from .env file if it exists
    # Validate environment variables (only for remote repos due to .env file - should be modified soon)
    if args.load_env_variables:
        load_dotenv()
        validate_env_vars()

    if args.binary_location:
        update_config(args.binary_location)

    should_monitor = args.enable_monitoring or monitoring_enabled()

    output_dir = args.output_dir
    if is_local:
        output_dir = output_dir or Path("./analysis")

    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created output directory: {output_dir}")

    initialize_codeboardingignore(output_dir)

    if is_local:
        process_local_repository(
            repo_path=args.local,
            output_dir=output_dir,
            project_name=args.project_name,
            depth_level=args.depth_level,
            component_name=args.partial_component,
            analysis_name=args.partial_analysis,
            monitoring_enabled=should_monitor,
            force_full=args.full,
        )
        logger.info(f"Documentation generated successfully in {output_dir}")
    else:
        if args.repositories:
            if args.upload:
                try:
                    store_token()
                except Exception as e:
                    logger.warning(f"Could not store GitHub token: {e}")

            for repo in tqdm(args.repositories, desc="Generating docs for repos"):
                repo_name = get_repo_name(repo)

                base_name = args.project_name if args.project_name else repo_name
                run_id = generate_run_id(base_name)
                monitoring_dir = get_monitoring_run_dir(run_id, create=should_monitor)

                with monitor_execution(run_id=run_id, output_dir=str(monitoring_dir), enabled=should_monitor) as mon:
                    mon.step(f"processing_{repo_name}")

                    try:
                        process_remote_repository(
                            repo_url=repo,
                            output_dir=output_dir,
                            depth_level=args.depth_level,
                            upload=args.upload,
                            cache_check=not args.no_cache_check,
                            run_id=run_id,
                            monitoring_enabled=should_monitor,
                            force_full=args.full,
                        )
                    except Exception as e:
                        logger.error(f"Failed to process repository {repo}: {e}")
                        continue

            logger.info("All repositories processed successfully!")
        else:
            logger.error("No repositories specified")


if __name__ == "__main__":
    main()
