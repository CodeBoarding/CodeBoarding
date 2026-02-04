"""
Analysis manifest for incremental updates.

The manifest stores the state needed to perform incremental updates:
- file_to_component mapping for fast lookup
- base commit for diff comparison
- component metadata for validation
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from agents.agent_responses import AnalysisInsights

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "analysis_manifest.json"
MANIFEST_SCHEMA_VERSION = 1


class AnalysisManifest(BaseModel):
    """Persisted state for incremental updates."""

    schema_version: int = Field(default=MANIFEST_SCHEMA_VERSION)

    # Git state at time of analysis
    repo_state_hash: str = Field(description="Hash from get_repo_state_hash()")
    base_commit: str = Field(description="Commit hash at time of analysis")

    # Core lookup: file path (relative) -> component name
    file_to_component: dict[str, str] = Field(default_factory=dict)

    # Track which components have sub-analysis JSONs
    expanded_components: list[str] = Field(default_factory=list)

    def get_component_for_file(self, file_path: str) -> str | None:
        """Get the component that owns a file."""
        return self.file_to_component.get(file_path)

    def get_files_for_component(self, component_name: str) -> list[str]:
        """Get all files belonging to a component."""
        return [f for f, c in self.file_to_component.items() if c == component_name]

    def get_all_components(self) -> set[str]:
        """Get set of all component names."""
        return set(self.file_to_component.values())

    def update_file_path(self, old_path: str, new_path: str) -> bool:
        """
        Update a file path (for renames). Returns True if updated.
        """
        if old_path in self.file_to_component:
            component = self.file_to_component.pop(old_path)
            self.file_to_component[new_path] = component
            return True
        return False

    def remove_file(self, file_path: str) -> str | None:
        """Remove a file from the manifest. Returns component name if found."""
        return self.file_to_component.pop(file_path, None)

    def add_file(self, file_path: str, component_name: str) -> None:
        """Add a new file to a component."""
        self.file_to_component[file_path] = component_name


def build_manifest_from_analysis(
    analysis: AnalysisInsights,
    repo_state_hash: str,
    base_commit: str,
    expanded_components: list[str] | None = None,
) -> AnalysisManifest:
    """
    Build a manifest from an AnalysisInsights object.

    Args:
        analysis: The analysis containing components with assigned_files
        repo_state_hash: Current repo state hash
        base_commit: Current commit hash
        expanded_components: List of component names that have sub-analysis JSONs

    Returns:
        AnalysisManifest with file_to_component mapping
    """
    file_to_component: dict[str, str] = {}

    for component in analysis.components:
        for file_path in component.assigned_files:
            # Normalize path (remove leading ./ if present)
            normalized_path = file_path.lstrip("./")
            file_to_component[normalized_path] = component.name

    return AnalysisManifest(
        repo_state_hash=repo_state_hash,
        base_commit=base_commit,
        file_to_component=file_to_component,
        expanded_components=expanded_components or [],
    )


def save_manifest(manifest: AnalysisManifest, output_dir: Path) -> Path:
    """
    Save manifest to the output directory.

    Returns the path to the saved manifest.
    """
    manifest_path = output_dir / MANIFEST_FILENAME

    with open(manifest_path, "w") as f:
        f.write(manifest.model_dump_json(indent=2))

    logger.info(f"Saved analysis manifest to {manifest_path}")
    return manifest_path


def load_manifest(output_dir: Path) -> AnalysisManifest | None:
    """
    Load manifest from the output directory.

    Returns None if manifest doesn't exist or is invalid.
    """
    manifest_path = output_dir / MANIFEST_FILENAME

    if not manifest_path.exists():
        logger.debug(f"No manifest found at {manifest_path}")
        return None

    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)

        manifest = AnalysisManifest.model_validate(data)

        # Check schema version compatibility
        if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
            logger.warning(f"Manifest schema version mismatch: {manifest.schema_version} != {MANIFEST_SCHEMA_VERSION}")
            return None

        logger.info(f"Loaded manifest with {len(manifest.file_to_component)} file mappings")
        return manifest

    except Exception as e:
        logger.warning(f"Failed to load manifest: {e}")
        return None


def manifest_exists(output_dir: Path) -> bool:
    """Check if a manifest exists in the output directory."""
    return (output_dir / MANIFEST_FILENAME).exists()
