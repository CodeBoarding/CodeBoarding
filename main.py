import argparse
import json
import logging
import os
import shutil
from typing import Literal
from pathlib import Path

import requests
from tqdm import tqdm

from agents.llm_config import configure_models, validate_api_key_provided
from user_config import ensure_config_template, load_user_config
from core import get_registries, load_plugins
from diagram_analysis import DiagramGenerator, IncrementalAnalysisRequiresFullError, RunContext
from diagram_analysis.incremental_history import append_incremental_history_event, build_incremental_history_event
from diagram_analysis.incremental_models import IncrementalRunStats, IncrementalSummary
from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from diagram_analysis.io_utils import load_full_analysis, save_sub_analysis
from logging_config import setup_logging
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir
from output_generators.markdown import generate_markdown_file
from repo_utils import (
    clone_repository,
    get_branch,
    get_repo_name,
    store_token,
    upload_onboarding_materials,
)
from repo_utils.ignore import initialize_codeboardingignore
from utils import (
    create_temp_repo_folder,
    remove_temp_repo_folder,
    sanitize,
)
from vscode_constants import update_config

logger = logging.getLogger(__name__)
action_logger = logging.getLogger("actions")


def log_action(action: str, **fields: object) -> None:
    payload = {
        "event": "action",
        "action": action,
        **fields,
    }
    action_logger.info(json.dumps(payload, default=str, sort_keys=True))


def record_incremental_history(
    *,
    output_dir: Path,
    run_id: str,
    project_name: str,
    event_type: Literal["incremental_analysis", "baseline_reset"],
    status: Literal["completed", "requires_full_analysis"],
    message: str,
    summary: IncrementalSummary | None = None,
    stats: IncrementalRunStats | None = None,
) -> None:
    try:
        event = build_incremental_history_event(
            run_id=run_id,
            event_type=event_type,
            status=status,
            message=message,
            project_name=project_name,
            summary=summary,
            stats=stats,
        )
        append_incremental_history_event(output_dir, event)
    except Exception as exc:
        logger.warning("Failed to record incremental history: %s", exc)


def env_monitoring_enabled() -> bool:
    return os.getenv("ENABLE_MONITORING", "false").lower() in ("1", "true", "yes")


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
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    force_full: bool = False,
) -> Path:
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
    )
    generator.force_full_analysis = force_full
    return generator.generate_analysis()


def generate_markdown_docs(
    repo_name: str,
    repo_path: Path,
    repo_url: str,
    analysis_path: Path,
    output_dir: Path,
    demo_mode: bool = False,
):
    target_branch = get_branch(repo_path)
    repo_ref = f"{repo_url}/blob/{target_branch}/"

    # Load the single unified analysis.json
    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)

    # Generate markdown for root analysis
    root_expanded = set(sub_analyses.keys())
    generate_markdown_file(
        "on_boarding",
        root_analysis,
        repo_name,
        repo_ref=repo_ref,
        expanded_components=root_expanded,
        temp_dir=output_dir,
        demo=demo_mode,
    )

    # Build id-to-name mapping across all levels for file naming
    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)

    # Generate markdown for each sub-analysis
    for comp_id, sub_analysis in sub_analyses.items():
        sub_expanded = {c.component_id for c in sub_analysis.components if c.component_id in sub_analyses}
        comp_name = id_to_name.get(comp_id, comp_id)
        fname = sanitize(comp_name)
        generate_markdown_file(
            fname,
            sub_analysis,
            repo_name,
            repo_ref=repo_ref,
            expanded_components=sub_expanded,
            temp_dir=output_dir,
            demo=demo_mode,
        )


def partial_update(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    component_id: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
):
    """
    Update a specific component in an existing analysis.
    """
    log_action(
        "component_analysis_started",
        component_id=component_id,
        depth_level=depth_level,
        project_name=project_name,
        run_id=run_id,
    )
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=project_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
    )
    generator.pre_analysis()

    # Load the full unified analysis (root + all sub-analyses)
    full_analysis = load_full_analysis(output_dir)
    if full_analysis is None:
        logger.error(f"No analysis.json found in '{output_dir}'. Please ensure the file exists.")
        log_action(
            "component_analysis_failed",
            component_id=component_id,
            project_name=project_name,
            reason="analysis_missing",
            run_id=run_id,
        )
        return

    root_analysis, sub_analyses = full_analysis

    # Search root components first, then all nested sub-analysis components
    component_to_analyze = None
    for component in root_analysis.components:
        if component.component_id == component_id:
            logger.info(f"Updating analysis for component: {component.name}")
            component_to_analyze = component
            break
    if component_to_analyze is None:
        for sub_analysis in sub_analyses.values():
            for component in sub_analysis.components:
                if component.component_id == component_id:
                    logger.info(f"Updating analysis for component: {component.name}")
                    component_to_analyze = component
                    break
            if component_to_analyze is not None:
                break

    if component_to_analyze is None:
        logger.error(f"Component with ID '{component_id}' not found in analysis")
        log_action(
            "component_analysis_failed",
            component_id=component_id,
            project_name=project_name,
            reason="component_not_found",
            run_id=run_id,
        )
        return

    comp_id, sub_analysis, new_components = generator.process_component(component_to_analyze)

    if sub_analysis:
        save_sub_analysis(sub_analysis, output_dir, component_id)
        logger.info(f"Updated component '{component_id}' in analysis.json")
        log_action(
            "component_analysis_completed",
            component_id=component_id,
            new_components=len(new_components),
            project_name=project_name,
            run_id=run_id,
            sub_analysis_component_id=comp_id,
        )
    else:
        logger.error(f"Failed to generate sub-analysis for component '{component_id}'")
        log_action(
            "component_analysis_failed",
            component_id=component_id,
            project_name=project_name,
            reason="sub_analysis_generation_failed",
            run_id=run_id,
        )


def generate_docs_remote(
    repo_url: str,
    temp_repo_folder: Path,
    run_id: str,
    log_path: str,
    local_dev: bool = False,
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
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
    )


def process_remote_repository(
    repo_url: str,
    run_id: str,
    log_path: str,
    output_dir: Path | None = None,
    depth_level: int = 1,
    upload: bool = False,
    cache_check: bool = True,
    monitoring_enabled: bool = False,
):
    """
    Process a remote repository by cloning and generating documentation.
    """
    repo_root = Path("repos")
    repo_name = get_repo_name(repo_url)
    log_action(
        "remote_repository_started",
        cache_check=cache_check,
        depth_level=depth_level,
        output_dir=str(output_dir) if output_dir else None,
        repo_name=repo_name,
        repo_url=repo_url,
        run_id=run_id,
        upload=upload,
    )

    # Check cache if enabled
    if cache_check and onboarding_materials_exist(repo_name):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        log_action(
            "remote_cache_hit",
            repo_name=repo_name,
            repo_url=repo_url,
            run_id=run_id,
        )
        return

    # Clone repository
    log_action(
        "remote_clone_started",
        repo_name=repo_name,
        repo_url=repo_url,
        run_id=run_id,
    )
    repo_name = clone_repository(repo_url, repo_root)
    repo_path = repo_root / repo_name

    temp_folder = create_temp_repo_folder()

    try:
        log_action(
            "analysis_started",
            depth_level=depth_level,
            mode="remote",
            repo_name=repo_name,
            repo_path=str(repo_path),
            run_id=run_id,
        )
        analysis_path = generate_analysis(
            repo_name=repo_name,
            repo_path=repo_path,
            output_dir=temp_folder,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
            monitoring_enabled=monitoring_enabled,
        )
        log_action(
            "analysis_completed",
            analysis_path=str(analysis_path),
            mode="remote",
            repo_name=repo_name,
            run_id=run_id,
        )

        # Generate markdown documentation for remote repo
        log_action(
            "markdown_generation_started",
            analysis_path=str(analysis_path),
            repo_name=repo_name,
            run_id=run_id,
        )
        generate_markdown_docs(
            repo_name=repo_name,
            repo_path=repo_path,
            repo_url=repo_url,
            analysis_path=analysis_path,
            output_dir=temp_folder,
            demo_mode=True,
        )
        log_action(
            "markdown_generation_completed",
            repo_name=repo_name,
            run_id=run_id,
        )

        # Copy files to output directory if specified
        if output_dir:
            log_action(
                "output_copy_started",
                destination=str(output_dir),
                repo_name=repo_name,
                run_id=run_id,
                source=str(temp_folder),
            )
            copy_files(temp_folder, output_dir)
            log_action(
                "output_copy_completed",
                destination=str(output_dir),
                repo_name=repo_name,
                run_id=run_id,
            )

        # Upload if requested
        if upload:
            log_action(
                "upload_started",
                repo_name=repo_name,
                run_id=run_id,
                source=str(temp_folder),
            )
            upload_onboarding_materials(repo_name, temp_folder, "results")
            log_action(
                "upload_completed",
                repo_name=repo_name,
                run_id=run_id,
            )

        log_action(
            "remote_repository_completed",
            repo_name=repo_name,
            repo_url=repo_url,
            run_id=run_id,
        )
    finally:
        RunContext(run_id=run_id, log_path=log_path, repo_dir=repo_path).finalize()
        remove_temp_repo_folder(str(temp_folder))


def process_local_repository(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    component_id: str | None = None,
    monitoring_enabled: bool = False,
    incremental: bool = False,
    force_full: bool = False,
    reset_baseline: bool = False,
):
    # Handle partial updates
    if component_id:
        log_action(
            "local_repository_started",
            depth_level=depth_level,
            mode_detail="component_analysis",
            project_name=project_name,
            repo_path=str(repo_path),
            run_id=run_id,
        )
        partial_update(
            repo_path=repo_path,
            output_dir=output_dir,
            project_name=project_name,
            component_id=component_id,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
        )
        return

    if incremental and not force_full:
        log_action(
            "local_repository_started",
            depth_level=depth_level,
            mode_detail="incremental_analysis",
            project_name=project_name,
            repo_path=str(repo_path),
            run_id=run_id,
        )
        generator = DiagramGenerator(
            repo_location=repo_path,
            temp_folder=output_dir,
            repo_name=project_name,
            output_dir=output_dir,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
            monitoring_enabled=monitoring_enabled,
        )
        generator.force_full_analysis = force_full

        # Run trace-based incremental analysis against the previous checkpoint
        try:
            result = generator.generate_analysis_incremental()
        except IncrementalAnalysisRequiresFullError as e:
            logger.error(str(e))
            record_incremental_history(
                output_dir=output_dir,
                run_id=run_id,
                project_name=project_name,
                event_type="incremental_analysis",
                status="requires_full_analysis",
                message=str(e),
                summary=e.summary,
                stats=generator.last_incremental_run_stats,
            )
            log_action(
                "incremental_analysis_requires_full",
                incremental_summary=e.summary.to_dict() if e.summary is not None else None,
                project_name=project_name,
                reason=str(e),
                run_id=run_id,
            )
            raise SystemExit(str(e)) from e
        except RuntimeError as e:
            logger.error(str(e))
            raise SystemExit(1) from e
        logger.info(f"Incremental analysis completed: {result}")
        record_incremental_history(
            output_dir=output_dir,
            run_id=run_id,
            project_name=project_name,
            event_type="incremental_analysis",
            status="completed",
            message=(
                generator.last_incremental_summary.message
                if generator.last_incremental_summary is not None
                else "Incremental analysis completed."
            ),
            summary=generator.last_incremental_summary,
            stats=generator.last_incremental_run_stats,
        )
        log_action(
            "incremental_analysis_completed",
            project_name=project_name,
            result=str(result),
            run_id=run_id,
        )
        return

    if reset_baseline:
        # Run full analysis and save as new baseline checkpoint
        log_action(
            "local_repository_started",
            depth_level=depth_level,
            mode_detail="reset_baseline",
            project_name=project_name,
            repo_path=str(repo_path),
            run_id=run_id,
        )
        generator = DiagramGenerator(
            repo_location=repo_path,
            temp_folder=output_dir,
            repo_name=project_name,
            output_dir=output_dir,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
            monitoring_enabled=monitoring_enabled,
        )
        result = generator.generate_incremental_analysis_baseline()
        logger.info(f"Baseline reset completed: {result}")
        record_incremental_history(
            output_dir=output_dir,
            run_id=run_id,
            project_name=project_name,
            event_type="baseline_reset",
            status="completed",
            message="A full analysis completed and a new incremental baseline checkpoint was saved.",
            stats=generator.last_incremental_run_stats,
        )
        log_action(
            "baseline_reset_completed",
            project_name=project_name,
            result=str(result),
            run_id=run_id,
        )
        return

    # Full analysis (local repo - no markdown generation)
    log_action(
        "local_repository_started",
        depth_level=depth_level,
        force_full=force_full,
        mode_detail="full_analysis",
        project_name=project_name,
        repo_path=str(repo_path),
        run_id=run_id,
    )
    analysis_path = generate_analysis(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
        force_full=force_full,
    )
    log_action(
        "full_analysis_completed",
        analysis_path=str(analysis_path),
        force_full=force_full,
        project_name=project_name,
        run_id=run_id,
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

    # Validate partial update arguments
    if args.partial_component_id and not is_local:
        parser.error("--partial-component-id only works with local repositories")

    selected_modes = sum(
        [
            getattr(args, "full", False) is True,
            getattr(args, "incremental", False) is True,
            getattr(args, "reset_baseline", False) is True,
        ]
    )
    if selected_modes > 1:
        parser.error("Provide at most one of --full, --incremental, or --reset-baseline.")


def define_cli_arguments(parser: argparse.ArgumentParser):
    """
    Adds all command-line arguments and groups to the ArgumentParser.
    """
    parser.add_argument(
        "repositories",
        nargs="*",
        help="One or more Git repository URLs to generate documentation for",
    )
    parser.add_argument("--local", type=Path, help="Path to a local repository")

    # Partial update options
    parser.add_argument(
        "--partial-component-id",
        type=str,
        help="Component ID to update (for partial updates only)",
    )

    # Binary/tool configuration
    parser.add_argument(
        "--binary-location",
        type=Path,
        help="Path to the binary directory for language servers (overrides ~/.codeboarding/servers/)",
    )

    # Analysis options
    parser.add_argument(
        "--depth-level",
        type=int,
        default=1,
        help="Depth level for diagram generation (default: 1)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload onboarding materials to GeneratedOnBoardings repo (remote repos only)",
    )
    parser.add_argument("--enable-monitoring", action="store_true", help="Enable monitoring")

    # Incremental update options
    analysis_mode_group = parser.add_mutually_exclusive_group()
    analysis_mode_group.add_argument(
        "--full",
        action="store_true",
        help="Force full analysis, skipping checkpoint-backed incremental comparison",
    )
    analysis_mode_group.add_argument(
        "--incremental",
        action="store_true",
        help="Run trace-based incremental analysis against the previous checkpoint",
    )
    analysis_mode_group.add_argument(
        "--reset-baseline",
        action="store_true",
        help="Run a full analysis and save it as the new baseline checkpoint for future incremental runs",
    )


def main():
    """Main entry point for the unified CodeBoarding CLI."""
    parser = argparse.ArgumentParser(
        description="Generate onboarding documentation for Git repositories (local or remote)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local repository (output written to <repo>/.codeboarding/)
  codeboarding --local /path/to/repo

  # Local repository with custom depth level
  codeboarding --local /path/to/repo --depth-level 2

  # Remote repository (cloned to cwd/<repo_name>/, output to cwd/<repo_name>/.codeboarding/)
  codeboarding https://github.com/user/repo

  # Partial update (update single component by ID)
  codeboarding --local /path/to/repo --partial-component-id "1.2"

  # Checkpoint-backed incremental analysis
  codeboarding --local /path/to/repo --incremental

  # Force full analysis (skip checkpoint comparison)
  codeboarding --local /path/to/repo --full

  # Use custom binary location (e.g. VS Code extension)
  codeboarding --local /path/to/repo --binary-location /path/to/binaries
        """,
    )
    define_cli_arguments(parser)

    args = parser.parse_args()

    # Validate interdependent arguments
    is_local = args.local is not None
    validate_arguments(args, parser, is_local)

    # Derive output directory from repo path
    if is_local:
        local_repo_path = args.local.resolve()
        output_dir = local_repo_path / ".codeboarding"
    else:
        # Remote: use a shared .codeboarding dir in cwd so file logging starts immediately
        output_dir = Path.cwd() / ".codeboarding"
        output_dir.mkdir(parents=True, exist_ok=True)

    should_monitor = args.enable_monitoring or env_monitoring_enabled()

    # Setup logging
    setup_logging(log_dir=output_dir)
    log_action(
        "cli_start",
        binary_location=str(args.binary_location) if args.binary_location else None,
        depth_level=args.depth_level,
        force_full=args.full,
        incremental=args.incremental,
        mode="local" if is_local else "remote",
        monitoring_enabled=should_monitor,
        output_dir=str(output_dir),
        partial_component_id=args.partial_component_id,
        repo_path=str(local_repo_path) if is_local else None,
        repository_count=len(args.repositories) if args.repositories else 0,
        upload=args.upload,
    )

    # Ensure ~/.codeboarding/config.toml exists (writes template on first run)
    ensure_config_template()

    # Load ~/.codeboarding/config.toml: inject provider keys into env and store model overrides
    user_cfg = load_user_config()
    user_cfg.apply_to_env()
    configure_models(agent_model=user_cfg.llm.agent_model, parsing_model=user_cfg.llm.parsing_model)

    # Validate that an LLM provider key is configured before doing any heavy work
    try:
        validate_api_key_provided()
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1)

    load_plugins(get_registries())

    if args.binary_location:
        update_config(args.binary_location)
    else:
        from tool_registry import ensure_tools, needs_install

        if needs_install():
            logger.info("First run: downloading language server binaries to ~/.codeboarding/servers/ ...")
            ensure_tools(auto_install_npm=True, auto_install_vcpp=True)

    if is_local:
        output_dir.mkdir(parents=True, exist_ok=True)
        initialize_codeboardingignore(output_dir)

        # Derive project name from the repo directory name
        project_name = local_repo_path.name
        run_context = RunContext.resolve(
            repo_dir=local_repo_path,
            project_name=project_name,
            reuse_latest_run_id=args.incremental or args.partial_component_id is not None,
        )
        log_action(
            "run_context_resolved",
            mode="local",
            project_name=project_name,
            reuse_latest_run_id=args.incremental or args.partial_component_id is not None,
            run_id=run_context.run_id,
        )

        process_local_repository(
            repo_path=local_repo_path,
            output_dir=output_dir,
            project_name=project_name,
            depth_level=args.depth_level,
            component_id=args.partial_component_id,
            monitoring_enabled=should_monitor,
            incremental=args.incremental,
            force_full=args.full,
            reset_baseline=args.reset_baseline,
            run_id=run_context.run_id,
            log_path=run_context.log_path,
        )
        run_context.finalize()
        log_action(
            "cli_completed",
            mode="local",
            output_dir=str(output_dir),
            project_name=project_name,
            run_id=run_context.run_id,
        )
        logger.info(f"Documentation generated successfully in {output_dir}")
    else:
        if args.repositories:
            if args.upload:
                try:
                    store_token()
                except Exception as e:
                    logger.warning(f"Could not store GitHub token: {e}")

            repo_root = Path("repos")
            workspace_root = Path.cwd()

            for repo in tqdm(args.repositories, desc="Generating docs for repos"):
                repo_name = get_repo_name(repo)
                # Clone target: repos/<repo_name>; docs output: <cwd>/<repo_name>/.codeboarding
                repo_output_dir = workspace_root / repo_name / ".codeboarding"
                repo_output_dir.mkdir(parents=True, exist_ok=True)
                initialize_codeboardingignore(repo_output_dir)

                repo_cache_root = repo_root / repo_name
                run_context = RunContext.resolve(
                    repo_dir=repo_cache_root,
                    project_name=repo_name,
                    reuse_latest_run_id=True,
                )
                log_action(
                    "run_context_resolved",
                    mode="remote",
                    project_name=repo_name,
                    repo_url=repo,
                    reuse_latest_run_id=True,
                    run_id=run_context.run_id,
                )

                monitoring_dir = get_monitoring_run_dir(
                    run_context.log_path,
                    create=should_monitor,
                )

                with monitor_execution(
                    run_id=run_context.run_id,
                    output_dir=str(monitoring_dir),
                    enabled=should_monitor,
                ) as mon:
                    mon.step(f"processing_{repo_name}")

                    try:
                        process_remote_repository(
                            repo_url=repo,
                            run_id=run_context.run_id,
                            log_path=run_context.log_path,
                            output_dir=repo_output_dir,
                            depth_level=args.depth_level,
                            upload=args.upload,
                            monitoring_enabled=should_monitor,
                        )
                    except Exception as e:
                        logger.error(f"Failed to process repository {repo}: {e}")
                        continue

            log_action(
                "cli_completed",
                mode="remote",
                repository_count=len(args.repositories),
            )
            logger.info("All repositories processed successfully!")
        else:
            logger.error("No repositories specified")


if __name__ == "__main__":
    main()
