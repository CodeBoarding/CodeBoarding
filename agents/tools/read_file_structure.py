import logging
import os
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class DirInput(BaseModel):
    dir: Optional[str] = Field(
        default=".",  # or "" if you prefer
        description=(
            "Relative path to the directory whose file structure should be retrieved. "
            "Defaults to the project root if not specified (i.e., use '.' for root)."
        )
    )


class FileStructureTool(BaseTool):
    name: str = "getFileStructure"
    description: str = (
        "Returns the directory structure (as a tree) for a given subfolder or the root of the project. "
        "Useful for understanding project layout, file organization, or analyzing specific modules."
    )
    args_schema: Optional[ArgsSchema] = DirInput
    return_direct: bool = False
    cached_dirs: Optional[List[Path]] = None
    repo_dir: Optional[Path] = None

    def __init__(self, repo_dir: Path):
        super().__init__()
        self.repo_dir = repo_dir
        self.cached_dirs = [self.repo_dir]
        self.walk_dir(repo_dir)
        # Sort self.cached_dirs by depth:
        self.cached_dirs.sort(key=lambda x: len(x.parts))

    def walk_dir(self, root_project_dir):
        """
        Walk the directory and collect all directories
        """
        for file in os.listdir(root_project_dir):
            path = Path(root_project_dir) / file
            if path.is_dir():
                self.cached_dirs.append(path)
                self.walk_dir(path)

    def _run(self, dir: Optional[str] = None) -> str:

        """
        Run the tool with the given input.
        """
        if dir == ".":
            tree_structure = get_tree_string(self.repo_dir)
            tree_structure = "\n".join(tree_structure)
            return f"The file tree for {dir} is:\n{tree_structure}"

        dir = Path(dir)
        searching_dir = None
        for d in self.cached_dirs:
            # check if dir is a subdirectory of the cached directory
            if self.is_subsequence(dir, d):
                logging.info(f"[File Structure Tool] Found directory {d}")
                searching_dir = d
                break

        if searching_dir is None:
            dir = Path(*dir.parts[1:])
        for d in self.cached_dirs:
            # check if dir is a subdirectory of the cached directory
            if self.is_subsequence(dir, d):
                logging.info(f"[File Structure Tool] Found directory {d}")
                searching_dir = d
                break

        if searching_dir is None:
            # Try finding the dir with repo_dir without its first part
            logging.error(f"[File Structure Tool] Directory {dir} not found in cached directories.")
            return f"Error: The specified directory does not exist or is empty. Available directories are: {', '.join([str(d) for d in self.cached_dirs])}"
        # now use the tree command to get the file structure
        logging.info(f"[File Structure Tool] Reading file structure for {searching_dir}")
        tree_structure = get_tree_string(searching_dir)
        tree_structure = "\n".join(tree_structure)
        return f"The file tree for {dir} is:\n{tree_structure}"

    def is_subsequence(self, sub: Path, full: Path) -> bool:
        # exclude the analysis_dir from the comparison
        sub = sub.parts
        full = full.parts
        analysis_parts = self.repo_dir.parts
        full = full[len(analysis_parts):]
        for i in range(len(full) - len(sub) + 1):
            if full[i:i + len(sub)] == sub:
                return True
        return False


def get_tree_string(startpath, indent=''):
    tree_lines = []

    entries = sorted(os.listdir(startpath))
    for i, entry in enumerate(entries):
        path = os.path.join(startpath, entry)
        connector = '└── ' if i == len(entries) - 1 else '├── '
        tree_lines.append(indent + connector + entry)

        if os.path.isdir(path):
            extension = '    ' if i == len(entries) - 1 else '│   '
            subtree = get_tree_string(path, indent + extension)
            tree_lines.extend(subtree)

    return tree_lines
