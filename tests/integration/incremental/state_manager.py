"""Git state and checkpoint management for incremental analysis benchmarks.

Handles:
- Snapshotting the baseline commit and checkpoint state
- Applying change scenarios (edit files + commit)
- Restoring everything to the exact baseline state between runs
"""

import logging
import shutil
import subprocess
from pathlib import Path

from tests.integration.incremental.scenarios import ChangeScenario

logger = logging.getLogger(__name__)

CHECKPOINT_REF_PREFIX = "refs/codeboarding/checkpoints/"


class StateManager:
    """Manages repo state and checkpoint backup/restore between benchmark scenarios."""

    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir
        self.output_dir = repo_dir / ".codeboarding"
        self.baseline_commit: str = ""
        self._checkpoint_backup: Path | None = None
        self._baseline_refs: dict[str, str] = {}

    def verify_pinned_commit(self, expected_tag: str) -> None:
        """Warn if HEAD does not match *expected_tag*."""
        try:
            head = self._git("rev-parse", "HEAD").strip()
            tag_sha = self._git("rev-parse", f"refs/tags/{expected_tag}^{{}}").strip()
        except RuntimeError:
            logger.warning("Could not resolve tag %s in %s — skipping commit verification", expected_tag, self.repo_dir)
            return
        if head != tag_sha:
            logger.warning(
                "HEAD (%s) does not match pinned tag %s (%s). " "Scenarios may fail if file contents have changed.",
                head[:12],
                expected_tag,
                tag_sha[:12],
            )

    def snapshot_baseline(self) -> None:
        """Record current HEAD and backup the entire .codeboarding/ directory + git refs."""
        self.baseline_commit = self._git("rev-parse", "HEAD").strip()
        logger.info("Baseline commit: %s", self.baseline_commit)

        # Backup entire .codeboarding directory (untracked, would be wiped by git clean)
        if self.output_dir.exists():
            backup = self.repo_dir / "_codeboarding_backup"
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(
                self.output_dir, backup, symlinks=True, ignore=shutil.ignore_patterns("_codeboarding_backup")
            )
            self._checkpoint_backup = backup
            logger.info(".codeboarding backed up to %s", backup)

        # Snapshot codeboarding git refs
        self._baseline_refs = self._get_codeboarding_refs()
        logger.info("Snapshotted %d codeboarding refs", len(self._baseline_refs))

    def apply_scenario(self, scenario: ChangeScenario) -> None:
        """Apply file edits and commit in the target repo."""
        for edit in scenario.edits:
            file_path = self.repo_dir / edit.file_path

            if edit.action == "create":
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(edit.new_content or "")

            elif edit.action == "modify":
                if edit.content_fn is None:
                    raise ValueError(f"modify action requires content_fn for {edit.file_path}")
                old_content = file_path.read_text()
                new_content = edit.content_fn(old_content)
                if old_content == new_content:
                    raise ValueError(f"content_fn produced no change for {edit.file_path}")
                file_path.write_text(new_content)

            elif edit.action == "delete":
                if file_path.exists():
                    file_path.unlink()

        # Stage and commit
        paths_to_add = [edit.file_path for edit in scenario.edits if edit.action != "delete"]
        paths_to_rm = [edit.file_path for edit in scenario.edits if edit.action == "delete"]

        if paths_to_add:
            self._git("add", *paths_to_add)
        if paths_to_rm:
            self._git("rm", "--cached", *paths_to_rm)

        self._git("commit", "-m", scenario.commit_message)
        logger.info("Applied scenario '%s' and committed", scenario.name)

    def restore_baseline(self) -> None:
        """Reset repo to baseline commit and restore .codeboarding + checkpoint state."""
        # Reset working tree (exclude .codeboarding and backup from clean)
        self._git("reset", "--hard", self.baseline_commit)
        self._git("clean", "-fd", "-e", ".codeboarding", "-e", "_codeboarding_backup")

        # Restore entire .codeboarding directory from backup
        if self._checkpoint_backup and self._checkpoint_backup.exists():
            if self.output_dir.exists():
                shutil.rmtree(self.output_dir)
            shutil.copytree(self._checkpoint_backup, self.output_dir, symlinks=True)

        # Restore git refs: remove any new refs, restore original ones
        current_refs = self._get_codeboarding_refs()
        for ref_name in current_refs:
            if ref_name not in self._baseline_refs:
                self._git("update-ref", "-d", ref_name)

        for ref_name, ref_sha in self._baseline_refs.items():
            try:
                current_sha = self._git("rev-parse", "--verify", ref_name).strip()
            except RuntimeError:
                current_sha = ""
            if current_sha != ref_sha:
                self._git("update-ref", ref_name, ref_sha)

        logger.info("Restored baseline at %s", self.baseline_commit[:8])

    def cleanup(self) -> None:
        """Remove backup directory."""
        if self._checkpoint_backup and self._checkpoint_backup.exists():
            shutil.rmtree(self._checkpoint_backup)
            self._checkpoint_backup = None

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def _get_codeboarding_refs(self) -> dict[str, str]:
        """Return {ref_name: sha} for all codeboarding checkpoint refs."""
        try:
            output = self._git("for-each-ref", "--format=%(refname) %(objectname)", CHECKPOINT_REF_PREFIX)
        except RuntimeError:
            return {}
        refs = {}
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) == 2:
                refs[parts[0]] = parts[1]
        return refs
