import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class FileChange:
    """
    Container for the changes made to a single file.
    """

    filename: str
    additions: int
    deletions: int
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)

    def llm_str(self):
        """
        Returns a string representation of the file change suitable for LLM processing.
        """
        return f"File: {self.filename}, Added lines: +{self.additions}, Removed lines: -{self.deletions}"


def get_git_diff(repo_dir: Path, version: str) -> List[FileChange]:
    """
    Get the git diff between a specific version and the current working tree (uncommitted changes included).

    :param repo_dir: Path to the repository directory.
    :param version: The commit hash or tag to compare against.
    :return: A list of FileChange objects describing the differences.
    """
    changes: List[FileChange] = []

    try:
        from git import Repo

        repo = Repo(repo_dir)

        # Compare the specified version to the working tree (including staged + unstaged changes)
        diff_index = repo.git.diff(version, "--patch")

        # Group diff by file using parsing logic
        current_file = None
        added, removed = [], []
        for line in diff_index.splitlines():
            if line.startswith("diff --git"):
                # Save previous file change, if any
                if current_file:
                    changes.append(current_file)
                # Start a new file
                added, removed = [], []
                filename = line.split(" b/")[-1]
                current_file = FileChange(filename=filename, additions=0, deletions=0)
            elif line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
                continue
            elif line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])
            if current_file:
                current_file.additions = len(added)
                current_file.deletions = len(removed)
                current_file.added_lines = added
                current_file.removed_lines = removed

        # Append the last file if it exists
        if current_file:
            changes.append(current_file)

    except Exception as e:
        logging.error(f"Error obtaining git diff: {e}")
    return changes
