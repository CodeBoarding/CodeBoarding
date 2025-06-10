#!/usr/bin/env python3

import re
import shutil
import subprocess
import sys
from pathlib import Path


def extract_repo_name(ssh_url):
    """Extract repository name from SSH clone URL"""
    # Pattern to match SSH URL: git@github.com:user/repo.git
    pattern = r'git@github\.com:[^/]+/(.+?)(?:\.git)?$'
    match = re.search(pattern, ssh_url)

    if match:
        return match.group(1)
    else:
        raise ValueError(f"Invalid SSH URL format: {ssh_url}")


def run_command(command, cwd=None):
    """Run a shell command and return the result"""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {command}")
        print(f"Error: {e.stderr}")
        sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print("Usage: python pr_util.py <ssh_clone_url>")
        sys.exit(1)

    ssh_url = sys.argv[1]

    # Extract repository name
    repo_name = extract_repo_name(ssh_url)
    print(f"Repository name: {repo_name}")

    # Define paths
    forks_dir = Path("/home/ivan/StartUp/forks")
    repo_path = forks_dir / repo_name
    source_dir = Path(f"/home/ivan/StartUp/GeneratedOnBoardings/{repo_name}")
    target_dir = repo_path / ".codeboarding"

    # Create forks directory if it doesn't exist
    forks_dir.mkdir(parents=True, exist_ok=True)

    # Clone the repository
    print(f"Cloning repository to {repo_path}...")
    if repo_path.exists():
        print(f"Repository {repo_name} already exists. Removing...")
        shutil.rmtree(repo_path)

    run_command(f"git clone {ssh_url}", cwd=forks_dir)

    # Check if source directory exists
    if not source_dir.exists():
        print(f"Source directory does not exist: {source_dir}")
        sys.exit(1)

    # Create .codeboarding directory
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created directory: {target_dir}")

    # Copy markdown files
    print(f"Copying markdown files from {source_dir} to {target_dir}...")
    copied_files = []

    for item in source_dir.rglob("*.md"):
        if item.is_file():
            # Maintain directory structure
            relative_path = item.relative_to(source_dir)
            target_file = target_dir / relative_path

            # Create parent directories if needed
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            shutil.copy2(item, target_file)
            copied_files.append(str(relative_path))
            print(f"Copied: {relative_path}")

    if not copied_files:
        print("No markdown files found to copy.")
        sys.exit(0)

    # Git operations
    print("Adding files to git...")
    run_command("git add .codeboarding", cwd=repo_path)

    # # Check if there are changes to commit
    # try:
    #     run_command("git diff --cached --exit-code", cwd=repo_path)
    #     print("No changes to commit.")
    #     sys.exit(0)
    # except subprocess.CalledProcessError:
    #     # There are changes to commit
    #     pass

    print("Committing changes...")
    run_command('git commit -m "Added high-level diagrams"', cwd=repo_path)

    print("Pushing changes...")
    run_command("git push", cwd=repo_path)

    print(f"Successfully processed repository {repo_name}")
    print(f"Copied {len(copied_files)} markdown files")


if __name__ == "__main__":
    main()
