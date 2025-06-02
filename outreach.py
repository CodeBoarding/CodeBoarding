import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from github import Github
from git import Repo
import time
from generate_markdown import generate_docs_remote, generate_docs
from utils import create_temp_repo_folder

load_dotenv()
# GitHub token and organization name
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Set your GitHub token in the environment
ORGANIZATION = "CodeBoarding"  # Replace with your organization name


def fork_and_generate_docs(repo_url: str):
    if not GITHUB_TOKEN:
        raise ValueError("GitHub token not found. Set it in the environment as GITHUB_TOKEN.")

    # Authenticate with GitHub
    github = Github(GITHUB_TOKEN)
    user = github.get_user()

    # Extract repository owner and name from the URL
    repo_owner, repo_name = repo_url.rstrip("/").split("/")[-2:]

    # Fork the repository into the organization
    original_repo = github.get_repo(f"{repo_owner}/{repo_name}")
    org = github.get_organization(ORGANIZATION)

    forked_repo = original_repo.create_fork(organization=org.login)

    print(f"Forked repository: {forked_repo.clone_url}")

    # Clone the forked repository locally
    repo_root = Path(os.getenv("ROOT", "./"))
    repo_root.mkdir(parents=True, exist_ok=True)
    #
    local_repo_path = repo_root / "repos" / repo_name
    # Inject token into HTTPS URL for authentication
    time.sleep(5)  # Wait for the fork to be created
    authenticated_url = forked_repo.clone_url.replace(
        "https://", f"https://{GITHUB_TOKEN}@"
    )

    Repo.clone_from(authenticated_url, local_repo_path)

    print(f"Cloned repository to: {local_repo_path}")

    # Generate markdown files in the CodeBoarding folder
    codeboarding_dir = local_repo_path / "CodeBoarding"
    codeboarding_dir.mkdir(parents=True, exist_ok=True)
    temp_repo_folder = create_temp_repo_folder()
    generate_docs(repo_name, temp_repo_folder)
    print(f"Generated markdown files in: {codeboarding_dir}")
    # Copy the generated markdown files to the CodeBoarding directory
    for file in temp_repo_folder.glob("*.md"):
        shutil.copy(file, codeboarding_dir / file.name)

    # Commit and push the changes
    repo = Repo(local_repo_path)
    repo.git.add(A=True)
    repo.index.commit("Add CodeBoarding documentation")
    origin = repo.remote(name="origin")
    origin.push()
    print("Pushed changes to forked repository.")

    # Create a pull request to the original repository
    pr = original_repo.create_pull(
        title="Improve Onboarding with Abstract Diagrams",
        body="""This PR adds auto-generated high-level diagrams and component-level documentation using CodeBoarding, aimed at improving developer onboarding by making it easier to get to know the codebase and lowering the barrier to contribution.

We would love to hear what you think about the generated diagrams — feedback is more than welcome. If you are interested in using CodeBoarding for other projects or want to get in touch, feel free to reach out to me.""",
        head=f"{ORGANIZATION}:{original_repo.default_branch}",
        base=original_repo.default_branch
    )
    print(f"Created pull request: {pr.html_url}")


if __name__ == "__main__":
    repo_urls = ["https://github.com/openai/openai-python", "https://github.com/Rapptz/discord.py",
                 "https://github.com/stripe/stripe-python", "https://github.com/redis/redis-py",
                 "https://github.com/mongodb/mongo-python-driver", "https://github.com/getsentry/sentry-python",
                 "https://github.com/DataDog/datadog-api-client-python",
                 "https://github.com/koalalorenzo/python-digitalocean", "https://github.com/slackapi/python-slack-sdk",
                 "https://github.com/twilio/twilio-python", "https://github.com/fastapi/fastapi",
                 "https://github.com/valkey-io/valkey", "https://github.com/huggingface/transformers",
                 "https://github.com/psf/requests", "https://github.com/pallets/flask"]
    for repo_url in repo_urls:
        try:
            fork_and_generate_docs(repo_url)
        except Exception as e:
            print(f"Error processing {repo_url}: {e}")
            continue
