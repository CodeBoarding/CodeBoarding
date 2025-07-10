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
        "Returns project directory structure as a tree. "
        "**CONTEXTUAL USE** - Use only when project layout is unclear from existing context. "
        "Most effective for understanding overall project organization. "
        "**AVOID** recursive calls - use once for high-level structure understanding."
    )
    MAX_LINES: int = 500
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
            # Start with a reasonable depth limit
            max_depth = 10
            tree_lines = get_tree_string(self.repo_dir, max_depth=max_depth)

            # If we hit the line limit, try again with progressively lower depths
            while len(tree_lines) >= self.MAX_LINES and max_depth > 1:
                max_depth -= 1
                tree_lines = get_tree_string(self.repo_dir, max_depth=max_depth)

            tree_structure = "\n".join(tree_lines)
            depth_info = f" (limited to depth {max_depth})" if max_depth < 10 else ""
            return f"The file tree for {dir}{depth_info} is:\n{tree_structure}"

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

        # Start with a reasonable depth limit
        max_depth = 10
        tree_lines = get_tree_string(searching_dir, max_depth=max_depth)

        # If we hit the line limit, try again with progressively lower depths
        while len(tree_lines) >= 50000 and max_depth > 1:
            max_depth -= 1
            tree_lines = get_tree_string(searching_dir, max_depth=max_depth, max_lines=self.MAX_LINES)

        tree_structure = "\n".join(tree_lines)
        depth_info = f" (limited to depth {max_depth})" if max_depth < 10 else ""
        return f"The file tree for {dir}{depth_info} is:\n{tree_structure}"

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


def get_tree_string(startpath, indent='', max_depth=float('inf'), current_depth=0, max_lines=100):
    """
    Generate a tree-like string representation of the directory structure.
    
    Args:
        startpath: Path to start generating the tree from
        indent: Current indentation string
        max_depth: Maximum depth to traverse (default: unlimited)
        current_depth: Current depth in the traversal (used internally)
        max_lines: Maximum number of lines to generate
        
    Returns:
        List of strings representing the tree structure
    """
    tree_lines = []

    # Stop if we've exceeded max depth
    if current_depth > max_depth:
        return tree_lines

    try:
        entries = sorted(os.listdir(startpath))
    except (PermissionError, FileNotFoundError):
        # Handle permission errors or non-existent directories
        return [indent + "└── [Error reading directory]"]

    for i, entry in enumerate(entries):
        # Check if we've exceeded the maximum number of lines
        if len(tree_lines) >= max_lines:
            tree_lines.append(indent + "└── [Output truncated due to size limits]")
            return tree_lines

        path = os.path.join(startpath, entry)
        connector = '└── ' if i == len(entries) - 1 else '├── '
        tree_lines.append(indent + connector + entry)

        if os.path.isdir(path):
            extension = '    ' if i == len(entries) - 1 else '│   '
            subtree = get_tree_string(
                path,
                indent + extension,
                max_depth,
                current_depth + 1,
                max_lines - len(tree_lines)
            )
            tree_lines.extend(subtree)

            # Check again after adding subtree
            if len(tree_lines) >= max_lines:
                if tree_lines[-1] != indent + "└── [Output truncated due to size limits]":
                    tree_lines.append(indent + "└── [Output truncated due to size limits]")
                return tree_lines

    return tree_lines
