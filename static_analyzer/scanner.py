import json
import logging
import subprocess
from pathlib import Path
from typing import List, Set, Dict

from static_analyzer.programming_language import ProgrammingLanguage, ProgrammingLanguageBuilder
from utils import get_config

logger = logging.getLogger(__name__)


class ProjectScanner:
    def __init__(self, repo_location: Path):
        self.repo_location = repo_location

    def scan(self) -> List[ProgrammingLanguage]:
        """
        Scan the repository using Tokei and return parsed results.

        Returns:
            List[ProgrammingLanguage]: technologies with their sizes, percentages, and suffixes
        """

        commands = get_config("tools")["tokei"]["command"]
        result = subprocess.run(commands, cwd=self.repo_location, capture_output=True, text=True, check=True)

        server_config = get_config("lsp_servers")
        builder = ProgrammingLanguageBuilder(server_config)

        # Parse Tokei JSON output
        tokei_data = json.loads(result.stdout)

        # Compute total code count
        total_code = tokei_data.get("Total", {}).get("code", 0)
        if not total_code:
            logger.warning("No total code count found in Tokei output")
            return []

        # Use dict to accumulate and merge by lsp_key
        merged_languages: Dict[str, ProgrammingLanguage] = {}

        for technology, stats in tokei_data.items():
            if technology == "Total":
                continue

            code_count = stats.get("code", 0)
            if code_count == 0:
                continue

            percentage = code_count / total_code * 100

            # Extract suffixes if reports exist
            suffixes = set()
            for report in stats.get("reports", []):
                suffixes |= self._extract_suffixes([report["name"]])

            pl = builder.build(
                tokei_language=technology,
                code_count=code_count,
                percentage=percentage,
                file_suffixes=suffixes,
            )

            logger.debug(f"Found: {pl}")

            # Merge by lsp_key (or language if no lsp_key)
            key = pl.language.lower()
            if key in merged_languages:
                merged_languages[key] = merged_languages[key].merge(pl)
                logger.debug(f"Merged into: {merged_languages[key]}")
            else:
                merged_languages[key] = pl

        # Filter and return
        programming_languages = []
        for pl in merged_languages.values():
            logger.info(f"Found: {pl}")
            if pl.percentage >= 1:  # filter PL with less than 1% of code
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
