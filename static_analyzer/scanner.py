import json
import logging
import subprocess
from pathlib import Path
from typing import Set

from static_analyzer.programming_language import ProgrammingLanguage, ProgrammingLanguageBuilder
from utils import get_config

logger = logging.getLogger(__name__)


class ProjectScanner:
    def __init__(self, repo_location: Path):
        self.repo_location = repo_location
        self.all_text_files: list[str] = []

    def scan(self) -> list[ProgrammingLanguage]:
        """
        Scan the repository using Tokei and return parsed results.

        Also populates self.all_text_files with all text file paths found by Tokei.

        Returns:
            list[ProgrammingLanguage]: technologies with their sizes, percentages, and suffixes
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

        programming_languages: list[ProgrammingLanguage] = []
        all_files: list[str] = []
        for technology, stats in tokei_data.items():
            if technology == "Total":
                continue

            # Collect ALL text file paths from Tokei for file coverage,
            # including languages with code_count == 0 (e.g. Markdown is 100% comments)
            for report in stats.get("reports", []):
                all_files.append(report["name"])

            code_count = stats.get("code", 0)
            if code_count == 0:
                continue

            percentage = code_count / total_code * 100

            # Extract suffixes from reports
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
            if pl.percentage >= 1:
                programming_languages.append(pl)

        self.all_text_files = all_files
        return programming_languages

    @staticmethod
    def _extract_suffixes(files: list[str]) -> Set[str]:
        """
        Extract unique file suffixes from a list of files.

        Args:
            files (list[str]): list of file paths

        Returns:
            Set[str]: Unique file extensions/suffixes
        """
        suffixes = set()
        for file_path in files:
            suffix = Path(file_path).suffix
            if suffix:  # Only add non-empty suffixes
                suffixes.add(suffix)
        return suffixes
