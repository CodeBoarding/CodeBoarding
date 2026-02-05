import logging
from pathlib import Path
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field

from agents.constants import FileStructureConfig
from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class DirInput(BaseModel):
    dir: str | None = Field(
        default=".",
        description=(
            "Relative path to the directory whose file structure should be retrieved. "
            "Defaults to the project root if not specified (i.e., use '.' for root)."
        ),
    )


class FileStructureTool(BaseRepoTool):
    name: str = "getFileStructure"
    description: str = (
        "Returns project directory structure as a tree. "
        "Use only when project layout is unclear from existing context. "
        "Most effective for understanding overall project organization. "
        "Avoid recursive calls - use once for high-level structure understanding."
    )
    args_schema: ArgsSchema | None = DirInput
    return_direct: bool = False

    @property
    def cached_dirs(self) -> list[Path]:
        dirs = self.context.get_directories()
        # Ensure they are sorted by depth for is_subsequence logic
        return sorted(dirs, key=lambda x: len(x.parts))

    def _run(self, dir: str | None = None) -> str:
        """
        Run the tool with the given input.
        """
        if dir == "." and self.repo_dir:
            # Start with a reasonable depth limit
            max_depth = 10
            tree_lines = get_tree_string(self.repo_dir, max_depth=max_depth, ignore_manager=self.ignore_manager)

            # If we hit the line limit, try again with progressively lower depths
            while len(tree_lines) >= FileStructureConfig.MAX_LINES and max_depth > 1:
                max_depth -= 1
                tree_lines = get_tree_string(self.repo_dir, max_depth=max_depth, ignore_manager=self.ignore_manager)

            tree_structure = "\n".join(tree_lines)
            depth_info = f" (limited to depth {max_depth})" if max_depth < 10 else ""
            return f"The file tree for {dir}{depth_info} is:\n{tree_structure}"

        if not dir:
            return "Error: No directory specified."

        dir_path = Path(dir)
        searching_dir: Path | None = None
        if self.cached_dirs:
            for d in self.cached_dirs:
                # check if dir is a subdirectory of the cached directory
                if self.is_subsequence(dir_path, d):
                    logger.info(f"[File Structure Tool] Found directory {d}")
                    searching_dir = d
                    break

            if searching_dir is None:
                dir_path = Path(*dir_path.parts[1:])
                for d in self.cached_dirs:
                    # check if dir is a subdirectory of the cached directory
                    if self.is_subsequence(dir_path, d):
                        logger.info(f"[File Structure Tool] Found directory {d}")
                        searching_dir = d
                        break

        if searching_dir is None:
            logger.error(f"[File Structure Tool] Directory {dir} not found in cached directories.")
            cached_str = ", ".join([str(d) for d in self.cached_dirs]) if self.cached_dirs else "None"
            return f"Error: The specified directory does not exist or is empty. Available directories are: {cached_str}"

        logger.info(f"[File Structure Tool] Reading file structure for {searching_dir}")

        # Start with a reasonable depth limit
        max_depth = 10
        tree_lines = get_tree_string(searching_dir, max_depth=max_depth, ignore_manager=self.ignore_manager)

        # If we hit the line limit, try again with progressively lower depths
        while len(tree_lines) >= 50000 and max_depth > 1:
            max_depth -= 1
            tree_lines = get_tree_string(
                searching_dir, max_depth=max_depth, max_lines=FileStructureConfig.MAX_LINES, ignore_manager=self.ignore_manager
            )

        tree_structure = "\n".join(tree_lines)
        depth_info = f" (limited to depth {max_depth})" if max_depth < 10 else ""
        return f"The file tree for {dir}{depth_info} is:\n{tree_structure}"


def get_tree_string(
    startpath: Path,
    indent: str = "",
    max_depth: float = float("inf"),
    current_depth: int = 0,
    max_lines: int = 100,
    ignore_manager=None,
) -> list[str]:
    """
    Generate a tree-like string representation of the directory structure.
    """
    tree_lines: list[str] = []

    # Stop if we've exceeded max depth
    if current_depth > max_depth:
        return tree_lines

    try:
        # Filter entries using the ignore manager
        entries = sorted([p for p in startpath.iterdir() if not (ignore_manager and ignore_manager.should_ignore(p))])
    except (PermissionError, FileNotFoundError):
        # Handle permission errors or non-existent directories
        return [indent + "└── [Error reading directory]"]

    for i, entry_path in enumerate(entries):
        # Check if we've exceeded the maximum number of lines
        if len(tree_lines) >= max_lines:
            tree_lines.append(indent + "└── [Output truncated due to size limits]")
            return tree_lines

        connector = "└── " if i == len(entries) - 1 else "├── "
        tree_lines.append(indent + connector + entry_path.name)

        if entry_path.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            subtree = get_tree_string(
                entry_path,
                indent + extension,
                max_depth,
                current_depth + 1,
                max_lines - len(tree_lines),
                ignore_manager=ignore_manager,
            )
            tree_lines.extend(subtree)

            # Check again after adding subtree
            if len(tree_lines) >= max_lines:
                if tree_lines[-1] != indent + "└── [Output truncated due to size limits]":
                    tree_lines.append(indent + "└── [Output truncated due to size limits]")
                return tree_lines

    return tree_lines
