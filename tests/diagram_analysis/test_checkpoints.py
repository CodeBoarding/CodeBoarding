import json
import subprocess
import tempfile
from pathlib import Path

from diagram_analysis.checkpoints import (
    get_latest_checkpoint,
    latest_checkpoint_dir,
    materialize_checkpoint_worktree,
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


def test_save_checkpoint_creates_linear_history_and_overwrites_latest_alias():
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

        first_analysis = (output_dir / "checkpoints" / first.checkpoint_id / "analysis.json").read_text(
            encoding="utf-8"
        )
        latest_analysis = (latest_checkpoint_dir(output_dir) / "analysis.json").read_text(encoding="utf-8")
        assert '"description": "first"' in first_analysis
        assert latest_analysis == first_analysis

        source_file.write_text("value = 2\n", encoding="utf-8")
        _write_output_artifacts(output_dir, description="second")
        second = save_checkpoint(repo, output_dir, run_id="run-2")

        parents = _git(repo, "rev-list", "--parents", "-n", "1", second.commit_sha).split()
        assert parents[0] == second.commit_sha
        assert parents[1] == first.commit_sha

        latest_metadata = get_latest_checkpoint(repo, output_dir)
        assert latest_metadata is not None
        assert latest_metadata.checkpoint_id == second.checkpoint_id
        assert latest_metadata.commit_sha == second.commit_sha

        latest_analysis = (latest_checkpoint_dir(output_dir) / "analysis.json").read_text(encoding="utf-8")
        first_analysis = (output_dir / "checkpoints" / first.checkpoint_id / "analysis.json").read_text(
            encoding="utf-8"
        )
        second_analysis = (output_dir / "checkpoints" / second.checkpoint_id / "analysis.json").read_text(
            encoding="utf-8"
        )
        assert '"description": "first"' in first_analysis
        assert '"description": "second"' in second_analysis
        assert latest_analysis == second_analysis

        checkpoint_tree = _git(repo, "ls-tree", "-r", "--name-only", second.commit_sha).splitlines()
        assert ".codeboarding/analysis.json" not in checkpoint_tree


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
