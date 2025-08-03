#!/usr/bin/env python3
import os
import sys
import subprocess
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

from logging_config import setup_logging

# Load environment variables and set paths
overload = load_dotenv
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
ROOT_RESULT = os.getenv("ROOT_RESULT", str(PROJECT_ROOT / "results"))
MKDOCS_CONFIG = PROJECT_ROOT / "mkdocs.yml"


def scan_repos(docs_dir: Path) -> dict:
    """
    Scan subdirectories of docs_dir and return a mapping:
      repo_name -> ordered list of (label, relative_path) for each markdown page.
    Does NOT generate any files in subdirectories.
    """
    repos = {}
    for repo_dir in sorted(docs_dir.iterdir()):
        if not repo_dir.is_dir():
            continue
        # Collect all markdown files in this repo directory
        md_files = sorted(repo_dir.glob("*.md"))
        if not md_files:
            continue
        pages = []
        for md in md_files:
            stem = md.stem.lower()
            rel_path = md.relative_to(docs_dir)
            if stem in ("index", "readme"):
                label = "Overview"
            else:
                label = md.stem.replace("_", " ").replace("-", " ").title()
            pages.append((label, str(rel_path)))
        repos[repo_dir.name] = pages
    return repos


def write_index_page(repos: dict, docs_dir: Path):
    """
    Overwrite docs/index.md (landing page) listing all repositories.
    Each repo title links directly to its on_boarding.md file.
    """
    docs_dir.mkdir(parents=True, exist_ok=True)
    index_file = docs_dir / "index.md"

    lines = ["# Generated Repositories", ""]
    lines.append("All of your locally generated repositories:")
    lines.append("")

    for repo_name in repos:
        onboarding_path = f"{repo_name}/on_boarding.md"
        lines.append(f"### [{repo_name}]({onboarding_path})")
        lines.append("")

    index_file.write_text("\n".join(lines), encoding="utf-8")
    logging.info(f"Wrote root index page: {index_file}")



def build_nav_from_dir(path: Path, base_path: Path) -> list:
    nav = []
    entries = sorted(path.iterdir())
    for entry in entries:
        if entry.is_dir():
            child_nav = build_nav_from_dir(entry, base_path)
            if child_nav:
                nav.append({entry.name: child_nav})
        elif entry.suffix == ".md":
            rel_path = entry.relative_to(base_path)
            label = entry.stem.replace("_", " ").replace("-", " ").title()
            if entry.stem.lower() in ("readme", "index"):
                label = "Overview"
            nav.append({label: str(rel_path).replace(os.sep, "/")})
    return nav



def write_mkdocs_config(repos: dict, docs_dir: Path):
    nav = []
    for repo_name, pages in repos.items():
        sub_nav = []
        for label, rel_path in pages:
            sub_nav.append({label: rel_path})
        nav.append({repo_name: sub_nav})

    config = {
        "site_name": "CodeBoarding",
        "docs_dir": os.path.relpath(str(docs_dir), str(PROJECT_ROOT)),
        "theme": {
            "name": "mkdocs",
            "logo": "icon.svg",
            "favicon": "icon.svg"
        },
        "nav": [
            {"Repositories": nav}
        ],
        "plugins": [
            "search",
            {"mermaid2": {"version": "10.0.2"}}
        ]
    }

    MKDOCS_CONFIG.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8"
    )
    logging.info(f"Wrote MkDocs config: {MKDOCS_CONFIG}")




def main():
    setup_logging()
    docs_dir = Path(ROOT_RESULT)

    if not docs_dir.is_dir():
        logging.error(f"Docs directory not found: {docs_dir}")
        logging.info("No repositories found in ROOT_RESULT dir.")
        sys.exit(1)

    # Scan repos without creating stub files
    repos = scan_repos(docs_dir)

    if not repos:
        logging.info("No repositories found in ROOT_RESULT dir.")
        sys.exit(0)

    # Write a single indexing file at root of docs
    write_index_page(repos, docs_dir)

    # Update MkDocs configuration
    write_mkdocs_config(repos, docs_dir)

    # Serve the documentation site
    logging.info("Starting MkDocs server...")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "serve"],
        cwd=str(PROJECT_ROOT),
        check=True
    )


if __name__ == '__main__':
    main()