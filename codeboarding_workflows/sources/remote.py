import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import requests

from codeboarding_workflows.sources.local import SourceContext
from repo_utils import clone_repository, get_repo_name, upload_onboarding_materials
from utils import copy_files, create_temp_repo_folder, remove_temp_repo_folder

logger = logging.getLogger(__name__)

_GENERATED_ONBOARDINGS_URL = "https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main"
_UPLOAD_RESULTS_DIR = "results"


def onboarding_materials_exist(project_name: str) -> bool:
    generated_repo_url = f"{_GENERATED_ONBOARDINGS_URL}/{project_name}"
    try:
        response = requests.get(generated_repo_url, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Cache probe failed for '%s' (%s); proceeding with generation.", project_name, exc)
        return False
    if response.status_code == 200:
        logger.info(f"Repository has already been generated, please check {generated_repo_url}")
        return True
    return False


@contextmanager
def remote_source(
    repo_url: str,
    output_dir: Path | None = None,
    cache_check: bool = True,
    upload: bool = False,
) -> Iterator[SourceContext | None]:
    """Clone *repo_url*, yield a :class:`SourceContext`, then cleanup.

    Yields ``None`` when the cache probe indicates a prior run already exists
    — callers should skip analysis in that case. The temp artifact folder is
    removed on exit; if *output_dir* is given, markdown/JSON artifacts are
    copied out before cleanup. When *upload* is true, artifacts are uploaded
    to the GeneratedOnBoardings repo on successful exit.
    """
    repo_name = get_repo_name(repo_url)

    if cache_check and onboarding_materials_exist(repo_name):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        yield None
        return

    repo_root = Path("repos")
    cloned_name = clone_repository(repo_url, repo_root)
    repo_path = repo_root / cloned_name
    temp_folder = create_temp_repo_folder()

    try:
        yield SourceContext(repo_path=repo_path, project_name=cloned_name, artifact_dir=temp_folder)

        if output_dir:
            artifacts = [*temp_folder.glob("*.md"), *temp_folder.glob("*.json")]
            if artifacts:
                copy_files(artifacts, output_dir)
            else:
                logger.warning("No markdown or JSON files found in %s", temp_folder)

        if upload:
            upload_onboarding_materials(cloned_name, temp_folder, _UPLOAD_RESULTS_DIR)
    finally:
        remove_temp_repo_folder(str(temp_folder))
