import json
import logging
import platform
import shlex
import subprocess
from pathlib import Path

from static_analyzer.programming_language import ProgrammingLanguage, ProgrammingLanguageBuilder
from telemetry.events import track_tech_stack
from tool_registry.paths import is_wsl
from utils import get_config

logger = logging.getLogger(__name__)


def _format_command(command: object) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, (list, tuple)):
        return shlex.join([str(part) for part in command])
    return str(command)


def _format_stderr(stderr: object) -> str:
    if stderr is None:
        return "no stderr output"
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    stderr_text = str(stderr).strip()
    return stderr_text or "no stderr output"


def _tokei_failure_message(
    repo_location: Path,
    command: object,
    reason: str,
    stderr: object,
    install_hint: bool,
) -> str:
    wsl_detected = is_wsl()
    if wsl_detected:
        guidance = (
            "WSL detected: if a Windows tokei binary is being invoked from WSL, "
            "install and run a Linux tokei binary inside WSL."
        )
    elif install_hint:
        guidance = (
            "Install tokei and ensure it is available on PATH, then verify that "
            "'tokei -o json' works in your terminal."
        )
    else:
        guidance = "Verify that 'tokei -o json' works in your terminal."

    return (
        f"{reason} for repository '{repo_location}'. "
        f"Platform: {platform.platform()}. "
        f"WSL detected: {'yes' if wsl_detected else 'no'}. "
        f"Command: {_format_command(command)}. "
        f"stderr: {_format_stderr(stderr)}. "
        f"{guidance}"
    )


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
        try:
            result = subprocess.run(commands, cwd=self.repo_location, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                _tokei_failure_message(
                    self.repo_location,
                    commands,
                    f"Tokei executable not found ({exc})",
                    None,
                    install_hint=True,
                )
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                _tokei_failure_message(
                    self.repo_location,
                    commands,
                    f"Tokei command failed with exit code {exc.returncode}",
                    exc.stderr,
                    install_hint=False,
                )
            ) from exc

        if not result.stdout:
            raise RuntimeError(
                _tokei_failure_message(
                    self.repo_location,
                    commands,
                    "Tokei produced no output",
                    result.stderr,
                    install_hint=False,
                )
            )

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
            if pl.is_supported_lang():
                programming_languages.append(pl)

        self.all_text_files = all_files
        track_tech_stack(self.repo_location, total_code, programming_languages)
        return programming_languages

    @staticmethod
    def _extract_suffixes(files: list[str]) -> set[str]:
        """
        Extract unique file suffixes from a list of files.

        Args:
            files (list[str]): list of file paths

        Returns:
            set[str]: Unique file extensions/suffixes
        """
        suffixes = set()
        for file_path in files:
            suffix = Path(file_path).suffix
            if suffix:  # Only add non-empty suffixes
                suffixes.add(suffix)
        return suffixes
