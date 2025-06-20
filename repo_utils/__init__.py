import os
import logging
import shutil
import subprocess
from pathlib import Path

from git import Git, GitCommandError, Repo

from repo_utils.errors import RepoDontExistError, NoGithubTokenFoundError


def sanitize_repo_url(repo_url: str) -> str:
    """
    Converts various formats of Git URLs to SSH format (e.g., git@github.com:user/repo.git).
    """
    if repo_url.startswith("git@") or repo_url.startswith("ssh://"):
        return repo_url  # already in SSH format
    elif repo_url.startswith("https://") or repo_url.startswith("http://"):
        # Convert HTTPS to SSH format
        parts = repo_url.rstrip("/").split("/")
        if "github.com" in parts:
            host_index = parts.index("github.com")
            user_repo = "/".join(parts[host_index + 1:])
            return f"git@github.com:{user_repo}.git"
        else:
            raise ValueError("Only GitHub SSH conversion is supported.")
    else:
        raise ValueError("Unsupported URL format.")


def remote_repo_exists(repo_url: str) -> bool:
    if repo_url is None:
        return False
    try:
        Git().ls_remote(repo_url)
        return True
    except GitCommandError as e:
        stderr = (e.stderr or "").lower()
        if "not found" in stderr or "repository not found" in stderr:
            return False
        # something else went wrong (auth, network); re-raise so caller can decide
        raise


def clone_repository(repo_url: str, target_dir: Path = Path("./repos")) -> str:
    repo_url = sanitize_repo_url(repo_url)
    if not remote_repo_exists(repo_url):
        raise RepoDontExistError()

    base = repo_url.rstrip("/").split("/")[-1]
    name, ext = os.path.splitext(base)
    repo_name = name

    dest = target_dir / repo_name
    if dest.exists():
        logging.info(f"Repository {repo_name} already exists at {dest}, pulling latest.")
        repo = Repo(dest)
        repo.remotes.origin.pull()
    else:
        logging.info(f"Cloning {repo_url} into {dest}")
        Repo.clone_from(repo_url, dest)
    logging.info("Cloning finished!")
    return repo_name


def store_token():
    if not os.environ.get('GITHUB_TOKEN'):  # Using .get() for safer access
        raise NoGithubTokenFoundError()
    logging.info(f"Setting up credentials with token: {os.environ['GITHUB_TOKEN'][:7]}")  # only first 7 for safety
    cred = (
        "protocol=https\n"
        "host=github.com\n"
        f"username=git\n"
        f"password={os.environ['GITHUB_TOKEN']}\n"
        "\n"
    ).encode()
    subprocess.run(["git", "credential", "approve"], input=cred)


def upload_onboarding_materials(project_name, output_dir, repo_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
    repo = Repo(repo_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_location = os.path.join(repo_dir, project_name)
    if os.path.exists(onboarding_repo_location):
        shutil.rmtree(onboarding_repo_location)
    os.makedirs(onboarding_repo_location)

    for filename in os.listdir(output_dir):
        if filename.endswith('.md') or filename.endswith('.svg'):
            shutil.copy(os.path.join(output_dir, filename), os.path.join(onboarding_repo_location, filename))
    # Now commit the changes
    repo.git.add(A=True)  # Equivalent to `git add .`
    repo.index.commit(f"Uploading onboarding materials for {project_name}")
    origin.push()
