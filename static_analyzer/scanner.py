import json
import logging
import subprocess
from pathlib import Path
from typing import List, Set

from static_analyzer.programming_language import ProgrammingLanguage

logger = logging.getLogger(__name__)


class ProjectScanner:
    def __init__(self, repo_location: Path):
        self.repo_location = repo_location

    def scan(self) -> List[ProgrammingLanguage]:
        """
        Scan the repository using github-linguist and return parsed results.
        
        Returns:
            Dict containing technologies with their sizes, percentages, files, and suffixes
        """
        result = subprocess.run(
            ['github-linguist', '-b', '-j'],
            cwd=self.repo_location,
            capture_output=True,
            text=True,
            check=True
        )

        # Parse JSON output
        linguist_data = json.loads(result.stdout)

        programming_languages = []
        for technology, info in linguist_data.items():
            suffixes = self._extract_suffixes(info.get('files', []))
            pl = ProgrammingLanguage(
                language=technology,
                size=info.get('size', 0),
                percentage=float(info.get('percentage', '0')),
                suffixes=list(suffixes)
            )
            logger.info(f"Found: {pl}")
            if pl.percentage >= 1:
                programming_languages.append(pl)
                logger.info(f"Added {pl}")

        return programming_languages

    @staticmethod
    def _extract_suffixes(files: List[str]) -> Set[str]:
        """
        Extract unique file suffixes from a list of files.
        
        Args:
            files (List[str]): List of file paths
            
        Returns:
            Set[str]: Unique file extensions/suffixes
        """
        suffixes = set()
        for file_path in files:
            suffix = Path(file_path).suffix
            if suffix:  # Only add non-empty suffixes
                suffixes.add(suffix)
        return suffixes
