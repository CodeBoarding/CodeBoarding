import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="codeboarding", description="CodeBoarding CLI")
    parser.add_argument("repo", nargs="?", help="GitHub repo URL to analyze")
    parser.add_argument("--output-dir", dest="output_dir", default="results", help="Output directory for diagrams")
    args = parser.parse_args()

    if not args.repo:
        parser.print_help()
        return 0

    # Defer heavy imports until invocation to keep CLI startup fast
    try:
        from dotenv import load_dotenv
        from agents.prompts import initialize_global_factory, PromptType, LLMType
        from logging_config import setup_logging
        from utils import create_temp_repo_folder, remove_temp_repo_folder
        from main import generate_docs_remote, copy_files
    except Exception as exc:  # pragma: no cover
        print(f"Failed to import runtime modules: {exc}")
        return 1

    load_dotenv()

    # Default environment fallbacks, mirroring main.py behavior
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"
    if not os.getenv("ROOT_RESULT"):
        os.environ["ROOT_RESULT"] = "results"

    setup_logging()
    initialize_global_factory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)

    temp_repo_folder = create_temp_repo_folder()
    try:
        repo_name = generate_docs_remote(args.repo, temp_repo_folder, local_dev=True)
        if args.output_dir:
            copy_files(temp_repo_folder, Path(args.output_dir))
    finally:
        remove_temp_repo_folder(temp_repo_folder)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


