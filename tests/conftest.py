from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


CHECKPOINT_REF_PREFIX = "refs/codeboarding/checkpoints/by-id"
LATEST_CHECKPOINT_REF = "refs/codeboarding/checkpoints/latest"


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    checkpoint_id: str
    commit_sha: str
    parent_commit_sha: str | None
    created_at: str
    run_id: str
    version: int = 1

    @property
    def checkpoint_commit(self) -> str:
        return self.commit_sha

    @property
    def parent_checkpoint_commit(self) -> str | None:
        return self.parent_commit_sha

    @property
    def checkpoint_ref(self) -> str:
        return f"{CHECKPOINT_REF_PREFIX}/{self.checkpoint_id}"


def _checkpoint_dir(output_dir: Path, checkpoint_id: str) -> Path:
    return output_dir / "checkpoints" / checkpoint_id


def _latest_dir(output_dir: Path) -> Path:
    return output_dir / "checkpoints" / "latest"


def _generate_checkpoint_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def _load_metadata(path: Path) -> CheckpointMetadata | None:
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return CheckpointMetadata(
        checkpoint_id=payload["checkpoint_id"],
        commit_sha=payload["commit_sha"],
        parent_commit_sha=payload.get("parent_commit_sha"),
        created_at=payload["created_at"],
        run_id=payload["run_id"],
        version=payload.get("version", 1),
    )


def load_latest_checkpoint(output_dir: Path) -> CheckpointMetadata | None:
    return _load_metadata(_latest_dir(output_dir) / "metadata.json")


def get_latest_checkpoint(repo_dir: Path, output_dir: Path) -> CheckpointMetadata | None:
    latest = load_latest_checkpoint(output_dir)
    if latest is None:
        return None

    result = subprocess.run(
        ["git", "rev-parse", "--verify", LATEST_CHECKPOINT_REF],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    resolved_commit = result.stdout.strip()
    if resolved_commit == latest.commit_sha:
        return latest

    return CheckpointMetadata(
        checkpoint_id=latest.checkpoint_id,
        commit_sha=resolved_commit,
        parent_commit_sha=latest.parent_commit_sha,
        created_at=latest.created_at,
        run_id=latest.run_id,
        version=latest.version,
    )


def restore_latest_artifacts(output_dir: Path) -> Path | None:
    latest = _latest_dir(output_dir)
    analysis_path = latest / "analysis.json"
    if not analysis_path.is_file():
        return None

    for filename in ("analysis.json", "file_coverage.json", "codeboarding_version.json"):
        source = latest / filename
        if source.is_file():
            shutil.copy2(source, output_dir / filename)

    health_src = latest / "health"
    if health_src.is_dir():
        shutil.copytree(health_src, output_dir / "health", dirs_exist_ok=True)

    return output_dir / "analysis.json"


def remove_legacy_manifest(output_dir: Path) -> None:
    manifest = output_dir / "analysis_manifest.json"
    if manifest.exists():
        manifest.unlink()


def save_checkpoint(repo_dir: Path, output_dir: Path, run_id: str) -> CheckpointMetadata:
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.is_file():
        raise FileNotFoundError(f"Cannot checkpoint without {analysis_path}")

    previous = get_latest_checkpoint(repo_dir, output_dir)
    checkpoint_id = _generate_checkpoint_id()
    target_dir = _checkpoint_dir(output_dir, checkpoint_id)
    target_dir.mkdir(parents=True, exist_ok=False)

    for filename in ("analysis.json", "file_coverage.json", "codeboarding_version.json"):
        source = output_dir / filename
        if source.is_file():
            shutil.copy2(source, target_dir / filename)

    if (output_dir / "health").is_dir():
        shutil.copytree(output_dir / "health", target_dir / "health")

    commit_sha = _create_checkpoint_commit(repo_dir, previous.commit_sha if previous else None, checkpoint_id)
    metadata = CheckpointMetadata(
        checkpoint_id=checkpoint_id,
        commit_sha=commit_sha,
        parent_commit_sha=previous.commit_sha if previous else None,
        created_at=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
    )

    _write_metadata(target_dir / "metadata.json", metadata)
    _write_latest_alias(output_dir, target_dir, metadata)
    _update_refs(repo_dir, metadata, previous)
    return metadata


def _write_latest_alias(output_dir: Path, source_dir: Path, metadata: CheckpointMetadata) -> None:
    latest_dir = _latest_dir(output_dir)
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)

    for entry in source_dir.iterdir():
        destination = latest_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)

    _write_metadata(latest_dir / "metadata.json", metadata)


def _write_metadata(path: Path, metadata: CheckpointMetadata) -> None:
    path.write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")


def _update_refs(repo_dir: Path, metadata: CheckpointMetadata, previous: CheckpointMetadata | None) -> None:
    subprocess.run(
        ["git", "update-ref", metadata.checkpoint_ref, metadata.commit_sha],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    latest_cmd = ["git", "update-ref", LATEST_CHECKPOINT_REF, metadata.commit_sha]
    if previous is not None:
        latest_cmd.append(previous.commit_sha)
    subprocess.run(latest_cmd, cwd=repo_dir, capture_output=True, text=True, check=True)


def _create_checkpoint_commit(repo_dir: Path, parent_commit_sha: str | None, checkpoint_id: str) -> str:
    fd, index_tmp = tempfile.mkstemp(prefix="codeboarding_checkpoint_index_")
    os.close(fd)
    index_path = Path(index_tmp)
    index_path.unlink(missing_ok=True)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(index_path)
    env.setdefault("GIT_AUTHOR_NAME", "CodeBoarding")
    env.setdefault("GIT_AUTHOR_EMAIL", "codeboarding@local")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])

    try:
        subprocess.run(
            ["git", "add", "-A", "."],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "rm", "-r", "--cached", "--ignore-unmatch", ".codeboarding"],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        tree_sha = subprocess.run(
            ["git", "write-tree"],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        commit_cmd = ["git", "commit-tree", tree_sha]
        if parent_commit_sha:
            commit_cmd.extend(["-p", parent_commit_sha])
        commit_cmd.extend(["-m", f"CodeBoarding checkpoint {checkpoint_id}"])
        return subprocess.run(
            commit_cmd,
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    finally:
        index_path.unlink(missing_ok=True)


def _install_checkpoint_stub() -> None:
    module = cast(Any, types.ModuleType("diagram_analysis.checkpoints"))
    module.CheckpointMetadata = CheckpointMetadata
    module.LATEST_CHECKPOINT_REF = LATEST_CHECKPOINT_REF
    module.get_latest_checkpoint = get_latest_checkpoint
    module.load_latest_checkpoint = load_latest_checkpoint
    module.restore_latest_artifacts = restore_latest_artifacts
    module.remove_legacy_manifest = remove_legacy_manifest
    module.save_checkpoint = save_checkpoint
    sys.modules["diagram_analysis.checkpoints"] = module


try:
    import diagram_analysis.checkpoints  # noqa: F401
except Exception:
    _install_checkpoint_stub()
