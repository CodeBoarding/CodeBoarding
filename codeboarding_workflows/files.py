import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def copy_files(temp_folder: Path, output_dir: Path) -> None:
    """Copy markdown and JSON files from temp folder to output directory."""
    markdown_files = list(temp_folder.glob("*.md"))
    json_files = list(temp_folder.glob("*.json"))

    all_files = markdown_files + json_files

    if not all_files:
        logger.warning(f"No markdown or JSON files found in {temp_folder}")
        return

    for file in all_files:
        dest_file = output_dir / file.name
        shutil.copy2(file, dest_file)
        logger.info(f"Copied {file.name} to {dest_file}")
