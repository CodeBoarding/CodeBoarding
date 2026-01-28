"""Change classification for iterative analysis.

This module classifies file changes into categories that determine
the appropriate analysis strategy:
- Cosmetic: No re-analysis needed (whitespace, comments, formatting)
- Internal: Lightweight description update (implementation changes)
- Structural: Full re-analysis needed (API changes, new/deleted entities)
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from repo_utils.file_hash import compute_hash
from repo_utils.git_diff import get_file_content_at_commit
from static_analyzer.symbol_diff import SymbolDiff, SymbolDiffAnalyzer

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Classification of file changes for iterative analysis."""

    COSMETIC = "cosmetic"  # Whitespace, comments, formatting - no re-analysis needed
    INTERNAL = "internal"  # Implementation changes within existing functions - light update
    STRUCTURAL = "structural"  # API changes, new/deleted entities - full re-analysis
    NEW_FILE = "new_file"  # Brand new file - needs classification into component
    DELETED = "deleted"  # File removed - need to update component


@dataclass
class ClassifiedChange:
    """A classified file change with metadata for analysis decisions."""

    file_path: str
    change_type: ChangeType
    affected_components: list[str] = field(default_factory=list)  # Component names
    symbol_diff: SymbolDiff | None = None  # For non-cosmetic changes
    details: str = ""  # Human-readable summary

    # For moved/renamed files
    old_path: str | None = None
    is_move: bool = False

    def __str__(self) -> str:
        status = f"[{self.change_type.value}]"
        components = f" -> {', '.join(self.affected_components)}" if self.affected_components else ""
        move_info = f" (moved from {self.old_path})" if self.is_move else ""
        return f"{status} {self.file_path}{components}{move_info}: {self.details}"


@dataclass
class ChangeClassificationResult:
    """Complete result of change classification for a set of files."""

    classified_changes: list[ClassifiedChange] = field(default_factory=list)
    cosmetic_changes: list[ClassifiedChange] = field(default_factory=list)
    internal_changes: list[ClassifiedChange] = field(default_factory=list)
    structural_changes: list[ClassifiedChange] = field(default_factory=list)
    new_files: list[ClassifiedChange] = field(default_factory=list)
    deleted_files: list[ClassifiedChange] = field(default_factory=list)
    moved_files: list[ClassifiedChange] = field(default_factory=list)

    # Aggregate affected components
    components_needing_description_update: set[str] = field(default_factory=set)
    components_needing_full_reanalysis: set[str] = field(default_factory=set)
    components_with_deleted_files: set[str] = field(default_factory=set)

    @property
    def has_changes(self) -> bool:
        """Check if there are any non-cosmetic changes."""
        return bool(
            self.internal_changes or self.structural_changes or self.new_files or self.deleted_files or self.moved_files
        )

    @property
    def requires_llm(self) -> bool:
        """Check if any changes require LLM analysis."""
        return bool(self.internal_changes or self.structural_changes or self.new_files)

    def summary(self) -> str:
        """Generate a human-readable summary."""
        parts = []
        if self.cosmetic_changes:
            parts.append(f"{len(self.cosmetic_changes)} cosmetic")
        if self.internal_changes:
            parts.append(f"{len(self.internal_changes)} internal")
        if self.structural_changes:
            parts.append(f"{len(self.structural_changes)} structural")
        if self.new_files:
            parts.append(f"{len(self.new_files)} new files")
        if self.deleted_files:
            parts.append(f"{len(self.deleted_files)} deleted")
        if self.moved_files:
            parts.append(f"{len(self.moved_files)} moved")
        return ", ".join(parts) if parts else "no changes"


class ChangeClassifier:
    """Classifies file changes into Cosmetic, Internal, or Structural categories.

    This is the core of Phase 1 (Quick Triage) in the iterative analysis pipeline.
    It uses symbol-level diffing to determine whether changes affect the API
    or are just implementation details.
    """

    def __init__(
        self,
        repo_dir: Path,
        previous_analysis: AnalysisInsights,
        old_hashes: dict[str, str] | None = None,
    ):
        """Initialize the classifier.

        Args:
            repo_dir: Path to the repository
            previous_analysis: The previous AnalysisInsights to map files to components
            old_hashes: Optional cached hashes from previous analysis
        """
        self.repo_dir = repo_dir
        self.previous_analysis = previous_analysis
        self.old_hashes = old_hashes or {}
        self.symbol_diff_analyzer = SymbolDiffAnalyzer()
        self._file_to_components = self._build_file_component_map()

    def _build_file_component_map(self) -> dict[str, list[str]]:
        """Build reverse mapping: file_path -> component names."""
        mapping: dict[str, list[str]] = defaultdict(list)
        for component in self.previous_analysis.components:
            for file_path in component.assigned_files:
                # Normalize path for consistent matching
                normalized = self._normalize_path(file_path)
                mapping[normalized].append(component.name)
        return dict(mapping)

    def _normalize_path(self, file_path: str) -> str:
        """Normalize file path for consistent matching."""
        # Remove leading ./ if present
        if file_path.startswith("./"):
            file_path = file_path[2:]
        # Use forward slashes consistently
        return file_path.replace("\\", "/")

    def _get_affected_components(self, file_path: str) -> list[str]:
        """Get components affected by changes to a file."""
        normalized = self._normalize_path(file_path)
        return self._file_to_components.get(normalized, [])

    def classify_changes(
        self,
        added_files: list[str],
        modified_files: list[str],
        deleted_files: list[str],
        renamed_files: list[tuple[str, str]],  # (old_path, new_path)
        old_commit: str,
        new_hashes: dict[str, str] | None = None,
    ) -> ChangeClassificationResult:
        """Classify all changes from a git diff.

        Args:
            added_files: List of newly added file paths
            modified_files: List of modified file paths
            deleted_files: List of deleted file paths
            renamed_files: List of (old_path, new_path) tuples for renamed files
            old_commit: The commit hash to get old file contents from
            new_hashes: Optional pre-computed hashes for new files

        Returns:
            ChangeClassificationResult with all classified changes
        """
        result = ChangeClassificationResult()
        new_hashes = new_hashes or {}

        # Process new files
        for file_path in added_files:
            change = self._classify_new_file(file_path)
            result.classified_changes.append(change)
            result.new_files.append(change)

        # Process modified files
        for file_path in modified_files:
            change = self._classify_modified_file(file_path, old_commit, new_hashes.get(file_path))
            result.classified_changes.append(change)

            if change.change_type == ChangeType.COSMETIC:
                result.cosmetic_changes.append(change)
            elif change.change_type == ChangeType.INTERNAL:
                result.internal_changes.append(change)
                result.components_needing_description_update.update(change.affected_components)
            elif change.change_type == ChangeType.STRUCTURAL:
                result.structural_changes.append(change)
                result.components_needing_full_reanalysis.update(change.affected_components)

        # Process deleted files
        for file_path in deleted_files:
            change = self._classify_deleted_file(file_path)
            result.classified_changes.append(change)
            result.deleted_files.append(change)
            result.components_with_deleted_files.update(change.affected_components)

        # Process renamed/moved files
        for old_path, new_path in renamed_files:
            change = self._classify_renamed_file(old_path, new_path, old_commit)
            result.classified_changes.append(change)
            result.moved_files.append(change)
            # May need component reassignment
            if change.change_type == ChangeType.STRUCTURAL:
                result.components_needing_full_reanalysis.update(change.affected_components)

        return result

    def _classify_new_file(self, file_path: str) -> ClassifiedChange:
        """Classify a newly added file."""
        return ClassifiedChange(
            file_path=file_path,
            change_type=ChangeType.NEW_FILE,
            affected_components=[],  # Will be assigned during classification
            symbol_diff=None,
            details="New file added, needs component classification",
        )

    def _classify_deleted_file(self, file_path: str) -> ClassifiedChange:
        """Classify a deleted file."""
        affected = self._get_affected_components(file_path)
        return ClassifiedChange(
            file_path=file_path,
            change_type=ChangeType.DELETED,
            affected_components=affected,
            symbol_diff=None,
            details=f"File deleted from {len(affected)} component(s)",
        )

    def _classify_modified_file(
        self,
        file_path: str,
        old_commit: str,
        new_hash: str | None = None,
    ) -> ClassifiedChange:
        """Classify a modified file using symbol-level diff.

        Args:
            file_path: Path to the modified file
            old_commit: Commit hash to get old content from
            new_hash: Optional pre-computed hash of new content

        Returns:
            ClassifiedChange with appropriate type
        """
        affected = self._get_affected_components(file_path)
        full_path = self.repo_dir / file_path

        # Read new content
        try:
            new_content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.STRUCTURAL,
                affected_components=affected,
                details=f"Could not read file: {e}",
            )

        # Compute new hash if not provided
        if new_hash is None:
            new_hash = compute_hash(new_content)

        # Check if hash matches old hash (cosmetic change / false positive)
        old_hash = self.old_hashes.get(self._normalize_path(file_path))
        if old_hash and old_hash == new_hash:
            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.COSMETIC,
                affected_components=affected,
                details="No content change detected (hash match)",
            )

        # Get old content from git
        old_content = get_file_content_at_commit(self.repo_dir, file_path, old_commit)
        if old_content is None:
            # File didn't exist in old commit - treat as new
            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.NEW_FILE,
                affected_components=[],
                details="File not found in previous commit",
            )

        # Perform symbol-level diff
        symbol_diff = self.symbol_diff_analyzer.diff_symbols(file_path, old_content, new_content)

        # Classify based on symbol diff
        if symbol_diff.has_api_changes:
            details_parts = []
            if symbol_diff.added_symbols:
                names = [s.name for s in symbol_diff.added_symbols[:3]]
                details_parts.append(f"added: {', '.join(names)}")
            if symbol_diff.removed_symbols:
                names = [s.name for s in symbol_diff.removed_symbols[:3]]
                details_parts.append(f"removed: {', '.join(names)}")
            if symbol_diff.modified_signatures:
                names = [old.name for old, _ in symbol_diff.modified_signatures[:3]]
                details_parts.append(f"signature changes: {', '.join(names)}")

            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.STRUCTURAL,
                affected_components=affected,
                symbol_diff=symbol_diff,
                details="; ".join(details_parts) or "API changes detected",
            )
        elif symbol_diff.implementation_only:
            names = [s.name for s in symbol_diff.implementation_only[:3]]
            more = (
                f" (+{len(symbol_diff.implementation_only) - 3} more)"
                if len(symbol_diff.implementation_only) > 3
                else ""
            )
            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.INTERNAL,
                affected_components=affected,
                symbol_diff=symbol_diff,
                details=f"Implementation changes in: {', '.join(names)}{more}",
            )
        else:
            # No symbol changes detected - likely cosmetic
            return ClassifiedChange(
                file_path=file_path,
                change_type=ChangeType.COSMETIC,
                affected_components=affected,
                details="No semantic changes detected (comments/formatting)",
            )

    def _classify_renamed_file(
        self,
        old_path: str,
        new_path: str,
        old_commit: str,
    ) -> ClassifiedChange:
        """Classify a renamed/moved file.

        A renamed file is treated as:
        - Cosmetic if just the path changed (pure rename)
        - Structural if content also changed
        """
        old_affected = self._get_affected_components(old_path)
        new_affected = self._get_affected_components(new_path)
        all_affected = list(set(old_affected + new_affected))

        # Get old and new content to compare
        full_new_path = self.repo_dir / new_path
        try:
            new_content = full_new_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Could not read {new_path}: {e}")
            return ClassifiedChange(
                file_path=new_path,
                change_type=ChangeType.STRUCTURAL,
                affected_components=all_affected,
                old_path=old_path,
                is_move=True,
                details=f"Moved from {old_path}, could not verify content",
            )

        old_content = get_file_content_at_commit(self.repo_dir, old_path, old_commit)

        if old_content is None:
            return ClassifiedChange(
                file_path=new_path,
                change_type=ChangeType.NEW_FILE,
                affected_components=[],
                old_path=old_path,
                is_move=True,
                details=f"Moved from {old_path} but old content not found",
            )

        # Check if content is identical (pure rename)
        old_hash = compute_hash(old_content)
        new_hash = compute_hash(new_content)

        if old_hash == new_hash:
            # Pure rename - may need component reassignment
            return ClassifiedChange(
                file_path=new_path,
                change_type=ChangeType.COSMETIC,
                affected_components=all_affected,
                old_path=old_path,
                is_move=True,
                details=f"Renamed from {old_path} (content unchanged)",
            )
        else:
            # Rename with content changes
            symbol_diff = self.symbol_diff_analyzer.diff_symbols(new_path, old_content, new_content)
            change_type = ChangeType.STRUCTURAL if symbol_diff.has_api_changes else ChangeType.INTERNAL

            return ClassifiedChange(
                file_path=new_path,
                change_type=change_type,
                affected_components=all_affected,
                old_path=old_path,
                is_move=True,
                symbol_diff=symbol_diff,
                details=f"Moved from {old_path} with {symbol_diff.summary()}",
            )


def classify_change_for_file(
    file_path: str,
    old_content: str | None,
    new_content: str | None,
    old_hash: str | None = None,
    new_hash: str | None = None,
) -> ChangeType:
    """Standalone function to classify a single file change.

    This is a convenience function for simple use cases where you
    have the content directly.

    Args:
        file_path: Path to the file
        old_content: Previous content (None for new files)
        new_content: Current content (None for deleted files)
        old_hash: Optional pre-computed old hash
        new_hash: Optional pre-computed new hash

    Returns:
        ChangeType classification
    """
    # Handle new/deleted files
    if old_content is None:
        return ChangeType.NEW_FILE
    if new_content is None:
        return ChangeType.DELETED

    # Check hash match
    if old_hash is None:
        old_hash = compute_hash(old_content)
    if new_hash is None:
        new_hash = compute_hash(new_content)

    if old_hash == new_hash:
        return ChangeType.COSMETIC

    # Perform symbol diff
    analyzer = SymbolDiffAnalyzer()
    symbol_diff = analyzer.diff_symbols(file_path, old_content, new_content)

    if symbol_diff.has_api_changes:
        return ChangeType.STRUCTURAL
    elif symbol_diff.implementation_only:
        return ChangeType.INTERNAL
    else:
        return ChangeType.COSMETIC
