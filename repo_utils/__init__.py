import logging
import os
import shutil
import subprocess
from functools import wraps
from pathlib import Path
from typing import Optional, Any, Callable

from repo_utils.errors import RepoDontExistError, NoGithubTokenFoundError

logger = logging.getLogger(__name__)

# Handle the case where git is not installed on the system
try:
    from git import Repo, Git, GitCommandError

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    Repo = None
    Git = None
    GitCommandError = None


def require_git_import(default: Optional[Any] = None) -> Callable:
    """
    Decorator that ensures git module is available for a function.
    If git import fails and a default value is provided, returns that value.
    Otherwise, re-raises the ImportError.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not GIT_AVAILABLE:
                if default is not None:
                    logger.warning(f"Git module not available for {func.__name__}, returning default: {default}")
                    return default
                logger.error(f"Git module required for {func.__name__} but not installed")
                raise ImportError("GitPython is not installed. Install it with: pip install gitpython")
            return func(*args, **kwargs)

        return wrapper

    return decorator


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
            user_repo = "/".join(parts[host_index + 1 :])
            return f"git@github.com:{user_repo}.git"
        else:
            raise ValueError("Only GitHub SSH conversion is supported.")
    else:
        raise ValueError("Unsupported URL format.")


@require_git_import(default=False)
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
        raise e


def get_repo_name(repo_url: str):
    repo_url = sanitize_repo_url(repo_url)
    base = repo_url.rstrip("/").split("/")[-1]
    repo_name, _ = os.path.splitext(base)
    return repo_name


@require_git_import()
def clone_repository(repo_url: str, target_dir: Path = Path("./repos")) -> str:
    repo_url = sanitize_repo_url(repo_url)
    if not remote_repo_exists(repo_url):
        raise RepoDontExistError()

    repo_name = get_repo_name(repo_url)

    dest = target_dir / repo_name
    if dest.exists():
        logger.info(f"Repository {repo_name} already exists at {dest}, pulling latest.")
        repo = Repo(dest)
        repo.remotes.origin.pull()
    else:
        logger.info(f"Cloning {repo_url} into {dest}")
        Repo.clone_from(repo_url, dest)
    logger.info("Cloning finished!")
    return repo_name


@require_git_import()
def checkout_repo(repo_dir: Path, branch: str = "main") -> None:
    repo = Repo(repo_dir)
    if branch not in repo.heads:
        logger.info(f"Branch {branch} does not exist, creating it.")
        raise ValueError(f"Branch {branch} does not exist in the repository {repo_dir}: {repo.heads}")
    logger.info(f"Checking out branch {branch}.")
    repo.git.checkout(branch)
    repo.git.pull()  # Ensure we have the latest changes


def store_token():
    if not os.environ.get("GITHUB_TOKEN"):  # Using .get() for safer access
        raise NoGithubTokenFoundError()
    logger.info(f"Setting up credentials with token: {os.environ['GITHUB_TOKEN'][:7]}")  # only first 7 for safety
    cred = (
        "protocol=https\n" "host=github.com\n" f"username=git\n" f"password={os.environ['GITHUB_TOKEN']}\n" "\n"
    ).encode()
    subprocess.run(["git", "credential", "approve"], input=cred)


@require_git_import()
def upload_onboarding_materials(project_name, output_dir, repo_dir):
    repo = Repo(repo_dir)
    origin = repo.remote(name="origin")
    origin.pull()

    no_new_files = True
    for filename in os.listdir(output_dir):
        if filename.endswith(".md"):
            no_new_files = False
            break
    if no_new_files:
        logger.info(f"No new onboarding files to upload for {project_name}.")
        return

    onboarding_repo_location = os.path.join(repo_dir, project_name)
    if os.path.exists(onboarding_repo_location):
        shutil.rmtree(onboarding_repo_location)
    os.makedirs(onboarding_repo_location)

    for filename in os.listdir(output_dir):
        if filename.endswith(".md"):
            shutil.copy(os.path.join(output_dir, filename), os.path.join(onboarding_repo_location, filename))
    # Now commit the changes
    # Equivalent to `git add onboarding_repo_location .`.git.add(A=True)  # Equivalent to `git add .`
    repo.git.add(onboarding_repo_location, A=True)
    repo.index.commit(f"Uploading onboarding materials for {project_name}")
    origin.push()


@require_git_import(default="NoCommitHash")
def get_git_commit_hash(repo_dir: str) -> str:
    """
    Get the latest commit hash of the repository.
    """
    repo = Repo(repo_dir)
    return repo.head.commit.hexsha


@require_git_import(default="main")
def get_branch(repo_dir: Path) -> str:
    """
    Get the current branch name of the repository.
    """
    repo = Repo(repo_dir)
    return repo.active_branch.name if repo.active_branch else "main"
