import json
import subprocess
from pathlib import Path
from typing import List, Set

from static_analyzer.programming_language import ProgrammingLanguage


class ProjectScanner:
    def __init__(self, repo_location: Path):
        self.repo_location = repo_location

    def scan(self) -> List[ProgrammingLanguage]:
        """
        Scan the repository using github-linguist and return parsed results.
        
        Returns:
            Dict containing technologies with their sizes, percentages, files, and suffixes
        """
        try:
            # Execute github-linguist command
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
                programming_languages.append(
                    ProgrammingLanguage(
                        language=technology,
                        size=info.get('size', 0),
                        percentage=info.get('percentage', '0'),
                        suffixes=list(suffixes)
                    )
                )

            return programming_languages

        except subprocess.CalledProcessError as e:
            raise subprocess.CalledProcessError(f"Error running github-linguist: {e.stderr}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Error parsing JSON output: {e}")
        except FileNotFoundError:
            raise FileNotFoundError("github-linguist not found. Please ensure it's installed and in PATH")

    def _extract_suffixes(self, files: List[str]) -> Set[str]:
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
