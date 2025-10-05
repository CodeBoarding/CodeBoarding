import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


class TypeScriptConfigScanner:
    """
    Scanner for finding TypeScript/JavaScript configuration files in a repository.
    Supports multi-project setups (mono-repos) by finding all tsconfig.json and jsconfig.json files.
    """

    CONFIG_FILES = ['tsconfig.json', 'jsconfig.json']

    def __init__(self, repo_location: Path):
        self.repo_location = repo_location

    def find_typescript_projects(self) -> List[Path]:
        """
        Scan the repository for TypeScript/JavaScript configuration files.

        Returns:
            List[Path]: List of directories containing TypeScript/JavaScript projects (config file locations).
                        Each path is the directory containing a tsconfig.json or jsconfig.json.
        """
        project_roots = []
        seen_dirs = set()

        for config_file in self.CONFIG_FILES:
            # Find all config files recursively
            for config_path in self.repo_location.rglob(config_file):
                if config_path.is_file():
                    project_dir = config_path.parent

                    if project_dir not in seen_dirs:
                       seen_dirs.add(project_dir)
                       project_roots.append(project_dir)

        if not project_roots:
            logger.warning(f"No TypeScript configuration files found in {self.repo_location}")
        else:
            logger.info(f"Found {len(project_roots)} TypeScript project(s) in repository")

        return project_roots
