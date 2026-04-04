import json
import subprocess
import tempfile
from pathlib import Path

from diagram_analysis.persistence.checkpoints import (
    LATEST_CHECKPOINT_ARTIFACT_REF,
    get_latest_checkpoint,
    load_checkpoint,
    load_checkpoint_analysis,
    materialize_checkpoint_worktree,
    restore_latest_artifacts,
    save_checkpoint,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write_output_artifacts(output_dir: Path, *, description: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis = {
        "metadata": {
            "generated_at": "2026-03-31T00:00:00Z",
            "repo_name": "repo",
            "depth_level": 1,
        },
        "description": description,
        "components": [],
        "components_relations": [],
    }
    (output_dir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (output_dir / "codeboarding_version.json").write_text('{"commit_hash":"abc","code_boarding_version":"0.2.0"}')
    (output_dir / "file_coverage.json").write_text('{"version":1}')


def test_save_checkpoint_creates_linear_source_and_artifact_history():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")

        source_file = repo / "src" / "module.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("value = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "baseline")

        output_dir = repo / ".codeboarding"
        _write_output_artifacts(output_dir, description="first")
        first = save_checkpoint(repo, output_dir, run_id="run-1")

        assert first.artifact_commit_sha is not None
        first_analysis = _git(repo, "show", f"{first.artifact_commit_sha}:analysis.json")
        latest_analysis = _git(repo, "show", f"{LATEST_CHECKPOINT_ARTIFACT_REF}:analysis.json")
        assert '"description": "first"' in first_analysis
        assert latest_analysis == first_analysis
        assert not (output_dir / "checkpoints" / first.checkpoint_id).exists()

        source_file.write_text("value = 2\n", encoding="utf-8")
        _write_output_artifacts(output_dir, description="second")
        second = save_checkpoint(repo, output_dir, run_id="run-2")

        parents = _git(repo, "rev-list", "--parents", "-n", "1", second.commit_sha).split()
        assert parents[0] == second.commit_sha
        assert parents[1] == first.commit_sha
        assert second.artifact_commit_sha is not None
        artifact_parents = _git(repo, "rev-list", "--parents", "-n", "1", second.artifact_commit_sha).split()
        assert artifact_parents[0] == second.artifact_commit_sha
        assert artifact_parents[1] == first.artifact_commit_sha

        latest_metadata = get_latest_checkpoint(repo, output_dir)
        assert latest_metadata is not None
        assert latest_metadata.checkpoint_id == second.checkpoint_id
        assert latest_metadata.commit_sha == second.commit_sha
        assert latest_metadata.artifact_commit_sha == second.artifact_commit_sha

        latest_analysis = _git(repo, "show", f"{LATEST_CHECKPOINT_ARTIFACT_REF}:analysis.json")
        first_analysis = _git(repo, "show", f"{first.artifact_commit_sha}:analysis.json")
        second_analysis = _git(repo, "show", f"{second.artifact_commit_sha}:analysis.json")
        assert '"description": "first"' in first_analysis
        assert '"description": "second"' in second_analysis
        assert latest_analysis == second_analysis

        checkpoint_tree = _git(repo, "ls-tree", "-r", "--name-only", second.commit_sha).splitlines()
        assert ".codeboarding/analysis.json" not in checkpoint_tree
        artifact_tree = _git(repo, "ls-tree", "-r", "--name-only", second.artifact_commit_sha).splitlines()
        assert artifact_tree == ["analysis.json", "codeboarding_version.json", "file_coverage.json", "metadata.json"]


def test_materialize_checkpoint_worktree_creates_detached_checkout():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")

        source_file = repo / "src" / "module.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("value = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "baseline")

        output_dir = repo / ".codeboarding"
        _write_output_artifacts(output_dir, description="snapshot")
        checkpoint = save_checkpoint(repo, output_dir, run_id="run-1")

        worktree_path = materialize_checkpoint_worktree(repo, output_dir, checkpoint.checkpoint_id)
        assert (worktree_path / "src" / "module.py").read_text(encoding="utf-8") == "value = 1\n"
        assert not (worktree_path / ".codeboarding").exists()


def test_load_and_restore_checkpoint_artifacts_from_refs():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")

        source_file = repo / "src" / "module.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("value = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "baseline")

        output_dir = repo / ".codeboarding"
        _write_output_artifacts(output_dir, description="from-refs")
        checkpoint = save_checkpoint(repo, output_dir, run_id="run-1")

        metadata = load_checkpoint(output_dir, checkpoint.checkpoint_id, repo)
        assert metadata is not None
        assert metadata.checkpoint_id == checkpoint.checkpoint_id
        assert metadata.artifact_commit_sha == checkpoint.artifact_commit_sha

        analysis = load_checkpoint_analysis(output_dir, checkpoint.checkpoint_id, repo)
        assert analysis is not None
        root_analysis, _ = analysis
        assert root_analysis.description == "from-refs"

        (output_dir / "analysis.json").unlink()
        (output_dir / "codeboarding_version.json").unlink()
        (output_dir / "file_coverage.json").unlink()

        restored = restore_latest_artifacts(output_dir, repo)
        assert restored == output_dir / "analysis.json"
        assert json.loads((output_dir / "analysis.json").read_text(encoding="utf-8"))["description"] == "from-refs"


def test_load_checkpoint_falls_back_to_legacy_directory_metadata():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")

        output_dir = repo / ".codeboarding"
        legacy_dir = output_dir / "checkpoints" / "legacy-id"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "analysis.json").write_text(
            json.dumps(
                {
                    "metadata": {"repo_name": "repo", "depth_level": 1},
                    "description": "legacy",
                    "components": [],
                    "components_relations": [],
                }
            ),
            encoding="utf-8",
        )
        (legacy_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "checkpoint_id": "legacy-id",
                    "commit_sha": "abc123",
                    "parent_commit_sha": None,
                    "created_at": "2026-03-31T00:00:00Z",
                    "run_id": "legacy-run",
                    "version": 1,
                }
            ),
            encoding="utf-8",
        )

        metadata = load_checkpoint(output_dir, "legacy-id", repo)
        assert metadata is not None
        assert metadata.checkpoint_id == "legacy-id"
        assert metadata.commit_sha == "abc123"

        analysis = load_checkpoint_analysis(output_dir, "legacy-id", repo)
        assert analysis is not None
        root_analysis, _ = analysis
        assert root_analysis.description == "legacy"
