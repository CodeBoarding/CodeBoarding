#!/usr/bin/env python3

import re
import shutil
import subprocess
import sys
import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv


def load_github_token():
    """Load GitHub token from .env file"""
    env_path = Path("/home/ivan/StartUp/CodeBoarding/.env")
    if not env_path.exists():
        print(f"Error: .env file not found at {env_path}")
        sys.exit(1)

    load_dotenv(env_path)
    github_token = os.environ.get("GITHUB_TOKEN")

    if not github_token:
        print("Error: GITHUB_TOKEN not found in .env file")
        sys.exit(1)

    return github_token


def extract_repo_info_from_url(repo_url):
    """Extract repository owner and name from GitHub URL"""
    # Handle both HTTPS and SSH URLs

    # Handle HTTPS URL format: https://github.com/owner/repo
    https_pattern = r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$'
    https_match = re.search(https_pattern, repo_url)

    if https_match:
        return https_match.group(1), https_match.group(2)

    # Handle SSH URL format: git@github.com:owner/repo.git
    ssh_pattern = r'git@github\.com:([^/]+)/(.+?)(?:\.git)?$'
    ssh_match = re.search(ssh_pattern, repo_url)

    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    raise ValueError(f"Invalid GitHub URL format: {repo_url}")


def fork_repository(owner, repo, token, organization=None):
    """Fork the repository using GitHub API

    Args:
        owner (str): Owner of the original repository
        repo (str): Name of the repository
        token (str): GitHub token for authentication
        organization (str, optional): Organization to fork to. If None, forks to personal account.
    
    Returns:
        str: SSH URL of the forked repository
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/forks"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    data = {}
    if organization:
        data["organization"] = organization
        print(f"Forking repository {owner}/{repo} to organization {organization}...")
    else:
        print(f"Forking repository {owner}/{repo} to personal account...")

    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 202:
        fork_data = response.json()
        print(f"Repository forked successfully: {fork_data['full_name']}")
        time.sleep(5)  # Wait a bit for the fork to be created
        return fork_data['ssh_url']
    else:
        print(f"Error forking repository: {response.status_code}")
        print(response.json())
        sys.exit(1)


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


def update_markdown_links(file_path, original_owner, repo_url, repo_name):
    """
    Update links in markdown files to point to the correct repository
    
    Args:
        file_path (Path): Path to the markdown file
        original_owner (str): Original repository owner
        repo_url (str): Original repository URL
        repo_name (str): Repository name
    """
    if not file_path.exists():
        return False

    print(f"Updating links in {file_path}...")

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.readlines()

    # Pattern to match links like: https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{repo_name}/{file_path}
    def replace_github_url_in_line(line, replacement_base):
        pattern = r'https:\/github\.com\/(?:[^"/]+\/)+([^"]+\.md)'
        return re.sub(pattern, rf'{replacement_base}/\1', line)

    # Extract base URL (remove .git if present and ensure no trailing slash)
    base_repo_url = f"https://github.com/{original_owner}/{repo_name}/blob/main/.codeboarding/"
    final_content = []
    for line in content:
        # Replace links in the line
        new_line = line.replace("//", "/")
        updated_line = replace_github_url_in_line(new_line, base_repo_url)
        if updated_line != new_line:
            final_content.append(updated_line)
        else:
            final_content.append(line)
    content = "\n".join(final_content)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def main():
    if len(sys.argv) != 2:
        print("Usage: python pr_util.py <github_repo_url>")
        print("Example: python pr_util.py https://github.com/aqlaboratory/genie")
        sys.exit(1)

    # GitHub repository URL (either HTTPS or SSH)
    repo_url = sys.argv[1]

    # Extract repository owner and name
    original_owner, repo_name = extract_repo_info_from_url(repo_url)
    print(f"Repository: {original_owner}/{repo_name}")

    # Organization to fork to
    organization = "CodeBoarding"

    # Load GitHub token and fork repository
    github_token = load_github_token()
    forked_ssh_url = fork_repository(original_owner, repo_name, github_token, organization)

    # # Define paths
    forks_dir = Path("/home/ivan/StartUp/forks")
    repo_path = forks_dir / repo_name
    source_dir = Path(f"/home/ivan/StartUp/GeneratedOnBoardings/{repo_name}")
    target_dir = repo_path / ".codeboarding"

    # Create forks directory if it doesn't exist
    forks_dir.mkdir(parents=True, exist_ok=True)

    # Clone the forked repository
    print(f"Cloning forked repository to {repo_path}...")
    if repo_path.exists():
        print(f"Repository {repo_name} already exists. Removing...")
        shutil.rmtree(repo_path)

    run_command(f"git clone {forked_ssh_url}", cwd=forks_dir)

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

    # Update links in on_boarding.md if it exists
    on_boarding_file = target_dir / "on_boarding.md"
    update_markdown_links(on_boarding_file, original_owner, repo_url, repo_name)

    # Also update links in all other markdown files
    for md_file in target_dir.rglob("*.md"):
        if md_file != on_boarding_file:  # Skip if already processed
            update_markdown_links(md_file, original_owner, repo_url, repo_name)

    # Git operations
    print("Adding files to git...")
    run_command("git add .codeboarding", cwd=repo_path)

    commit_message = "Added high-level diagrams"

    print("Committing changes...")
    run_command(f'git commit -m "{commit_message}"', cwd=repo_path)

    print("Pushing changes...")
    run_command("git push", cwd=repo_path)

    print(f"Successfully processed repository {repo_name}")
    print(f"Copied {len(copied_files)} markdown files")


if __name__ == "__main__":
    main()
