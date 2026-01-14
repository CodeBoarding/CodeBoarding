"""Tests for DiffAnalyzingAgent, including language support synchronization."""

import pytest
import yaml
from pathlib import Path

from agents.constants import SUPPORTED_EXTENSIONS_FALLBACK


def get_config_extensions() -> set[str]:
    """
    Extract all file extensions from the static_analysis_config.yml configuration.

    Returns:
        Set of all file extensions defined in lsp_servers config
    """
    config_path = Path(__file__).parent.parent.parent / "static_analysis_config.yml"

    if not config_path.exists():
        pytest.skip(f"Configuration file not found: {config_path}")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    extensions = set()
    lsp_servers = config.get("lsp_servers", {})

    for server_config in lsp_servers.values():
        file_extensions = server_config.get("file_extensions", [])
        extensions.update(file_extensions)

    return extensions


def test_diff_analyzer_language_sync():
    """
    Test that SUPPORTED_EXTENSIONS_FALLBACK is in sync with static_analysis_config.yml.

    This test ensures that when new language support is added to the configuration,
    the SUPPORTED_EXTENSIONS_FALLBACK constant in constants.py is also updated.

    If this test fails, you need to:
    1. Check what new language extensions were added to static_analysis_config.yml
    2. Update SUPPORTED_EXTENSIONS_FALLBACK in agents/constants.py to include them
    3. Update the constant's comment to document the new language
    """
    config_extensions = get_config_extensions()

    # Check if config extensions are a subset of the fallback (we support everything configured)
    missing_from_fallback = config_extensions - SUPPORTED_EXTENSIONS_FALLBACK

    assert not missing_from_fallback, (
        f"New language extensions found in static_analysis_config.yml that are not in "
        f"SUPPORTED_EXTENSIONS_FALLBACK: {sorted(missing_from_fallback)}\n\n"
        f"Please update SUPPORTED_EXTENSIONS_FALLBACK in agents/constants.py to include these extensions."
    )

    # Also check if there are extensions in fallback that aren't in config (potential cleanup needed)
    extra_in_fallback = SUPPORTED_EXTENSIONS_FALLBACK - config_extensions

    if extra_in_fallback:
        # This is a warning, not a failure - extra extensions are harmless
        print(
            f"\nWARNING: Extensions in SUPPORTED_EXTENSIONS_FALLBACK that are not in config: "
            f"{sorted(extra_in_fallback)}\n"
            f"Consider removing them if the language support was removed."
        )


def test_supported_extensions_fallback_not_empty():
    """Test that the fallback constant is not empty."""
    assert SUPPORTED_EXTENSIONS_FALLBACK, "SUPPORTED_EXTENSIONS_FALLBACK should not be empty"


def test_supported_extensions_format():
    """Test that all extensions in the fallback start with a dot."""
    for ext in SUPPORTED_EXTENSIONS_FALLBACK:
        assert ext.startswith("."), f"Extension '{ext}' should start with a dot"
        assert len(ext) > 1, f"Extension '{ext}' should have at least one character after the dot"


def test_supported_languages_documented():
    """Test that we have the expected supported languages."""
    # These are the currently documented supported languages
    expected_languages = {
        "python": {".py", ".pyi"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
        "go": {".go"},
        "php": {".php"},
    }

    all_expected_extensions = set()
    for extensions in expected_languages.values():
        all_expected_extensions.update(extensions)

    assert (
        all_expected_extensions == SUPPORTED_EXTENSIONS_FALLBACK
    ), "SUPPORTED_EXTENSIONS_FALLBACK should match documented languages"
