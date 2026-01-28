from datetime import datetime

from pydantic import BaseModel, Field


class Version(BaseModel):
    commit_hash: str
    code_boarding_version: str


class IterativeAnalysisMetadata(BaseModel):
    """Metadata to support iterative analysis.

    This class stores the state needed to perform incremental updates
    to the analysis without re-analyzing the entire codebase.
    """

    commit_hash: str = Field(description="Git commit hash of last analysis")
    analysis_timestamp: datetime = Field(
        default_factory=datetime.now, description="Timestamp when the analysis was performed"
    )
    file_content_hashes: dict[str, str] = Field(
        default_factory=dict, description="Mapping of file_path -> SHA256 hash of normalized content"
    )

    def get_changed_files(self, new_hashes: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
        """Compare stored hashes with new hashes to identify changes.

        Args:
            new_hashes: Mapping of file_path -> SHA256 hash for current state

        Returns:
            Tuple of (added_files, modified_files, deleted_files)
        """
        old_files = set(self.file_content_hashes.keys())
        new_files = set(new_hashes.keys())

        added = list(new_files - old_files)
        deleted = list(old_files - new_files)

        # Modified = files that exist in both but have different hashes
        modified = [f for f in (old_files & new_files) if self.file_content_hashes[f] != new_hashes[f]]

        return added, modified, deleted
