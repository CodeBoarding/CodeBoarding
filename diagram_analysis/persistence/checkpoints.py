import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.persistence.analysis_json import parse_unified_analysis
from git import BadName, GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

logger = logging.getLogger(__name__)

CHECKPOINTS_DIRNAME = "checkpoints"
LATEST_DIRNAME = "latest"
METADATA_FILENAME = "metadata.json"
LEGACY_MANIFEST_FILENAME = "analysis_manifest.json"
CHECKPOINT_REF_PREFIX = "refs/codeboarding/checkpoints/by-id"
LATEST_CHECKPOINT_REF = "refs/codeboarding/checkpoints/latest"
CHECKPOINT_ARTIFACT_REF_PREFIX = "refs/codeboarding/checkpoints/artifacts/by-id"
LATEST_CHECKPOINT_ARTIFACT_REF = "refs/codeboarding/checkpoints/artifacts/latest"
ARTIFACT_FILENAMES = ("analysis.json", "codeboarding_version.json", "file_coverage.json")
HEALTH_ARTIFACT_PATH = Path("health") / "health_report.json"


@dataclass(frozen=True, slots=True)
class FileComponentIndex:
    file_to_component: dict[str, str] = field(default_factory=dict)

    def get_component_for_file(self, file_path: str) -> str | None:
        return self.file_to_component.get(file_path)

    def get_files_for_component(self, component_id: str) -> list[str]:
        return [file_path for file_path, owner in self.file_to_component.items() if owner == component_id]

    def get_all_components(self) -> set[str]:
        return set(self.file_to_component.values())

    def update_file_path(self, old_path: str, new_path: str) -> bool:
        if old_path not in self.file_to_component:
            return False
        component_id = self.file_to_component.pop(old_path)
        self.file_to_component[new_path] = component_id
        return True

    def remove_file(self, file_path: str) -> str | None:
        return self.file_to_component.pop(file_path, None)

    def add_file(self, file_path: str, component_id: str) -> None:
        self.file_to_component[file_path] = component_id


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    checkpoint_id: str
    commit_sha: str
    parent_commit_sha: str | None
    created_at: str
    run_id: str
    version: int = 2
    artifact_commit_sha: str | None = None
    parent_artifact_commit_sha: str | None = None

    @property
    def checkpoint_commit(self) -> str:
        return self.commit_sha

    @property
    def parent_checkpoint_commit(self) -> str | None:
        return self.parent_commit_sha

    @property
    def checkpoint_ref(self) -> str:
        return f"{CHECKPOINT_REF_PREFIX}/{self.checkpoint_id}"

    @property
    def artifact_checkpoint_ref(self) -> str:
        return f"{CHECKPOINT_ARTIFACT_REF_PREFIX}/{self.checkpoint_id}"


def build_file_component_index(root_analysis: AnalysisInsights) -> FileComponentIndex:
    file_to_component: dict[str, str] = {}
    for component in root_analysis.components:
        for file_group in component.file_methods:
            normalized_path = file_group.file_path.lstrip("./")
            file_to_component[normalized_path] = component.component_id
    return FileComponentIndex(file_to_component=file_to_component)


def checkpoints_root(output_dir: Path) -> Path:
    return output_dir / CHECKPOINTS_DIRNAME


def checkpoint_dir(output_dir: Path, checkpoint_id: str) -> Path:
    return checkpoints_root(output_dir) / checkpoint_id


def latest_checkpoint_dir(output_dir: Path) -> Path:
    return checkpoints_root(output_dir) / LATEST_DIRNAME


def remove_legacy_manifest(output_dir: Path) -> None:
    manifest_path = output_dir / LEGACY_MANIFEST_FILENAME
    if not manifest_path.exists():
        return

    manifest_path.unlink()
    logger.info("Removed legacy manifest %s", manifest_path)


def load_checkpoint(
    output_dir: Path,
    checkpoint_id: str,
    repo_dir: Path | None = None,
) -> CheckpointMetadata | None:
    resolved_repo = _resolve_repo_dir(output_dir, repo_dir)
    if resolved_repo is not None:
        metadata = _load_metadata_from_artifact_ref(resolved_repo, f"{CHECKPOINT_ARTIFACT_REF_PREFIX}/{checkpoint_id}")
        if metadata is not None:
            return metadata

    return _load_metadata(checkpoint_dir(output_dir, checkpoint_id) / METADATA_FILENAME)


def load_latest_checkpoint(output_dir: Path, repo_dir: Path | None = None) -> CheckpointMetadata | None:
    resolved_repo = _resolve_repo_dir(output_dir, repo_dir)
    if resolved_repo is not None:
        metadata = _load_metadata_from_artifact_ref(resolved_repo, LATEST_CHECKPOINT_ARTIFACT_REF)
        if metadata is not None:
            return metadata

    return _load_metadata(latest_checkpoint_dir(output_dir) / METADATA_FILENAME)


def get_latest_checkpoint(repo_dir: Path, output_dir: Path) -> CheckpointMetadata | None:
    latest = load_latest_checkpoint(output_dir, repo_dir)
    if latest is None:
        return None

    resolved_commit = _resolve_ref(repo_dir, LATEST_CHECKPOINT_REF)
    resolved_artifact_commit = _resolve_ref(repo_dir, LATEST_CHECKPOINT_ARTIFACT_REF)
    if resolved_commit is None:
        return latest
    if resolved_commit == latest.commit_sha and resolved_artifact_commit == latest.artifact_commit_sha:
        return latest

    return CheckpointMetadata(
        checkpoint_id=latest.checkpoint_id,
        commit_sha=resolved_commit,
        parent_commit_sha=latest.parent_commit_sha,
        created_at=latest.created_at,
        run_id=latest.run_id,
        version=latest.version,
        artifact_commit_sha=resolved_artifact_commit or latest.artifact_commit_sha,
        parent_artifact_commit_sha=latest.parent_artifact_commit_sha,
    )


def restore_latest_artifacts(output_dir: Path, repo_dir: Path | None = None) -> Path | None:
    resolved_repo = _resolve_repo_dir(output_dir, repo_dir)
    artifact_commit_sha = _resolve_ref(resolved_repo, LATEST_CHECKPOINT_ARTIFACT_REF) if resolved_repo else None
    if resolved_repo is not None and artifact_commit_sha is not None:
        _restore_artifacts_from_commit(output_dir, resolved_repo, artifact_commit_sha)
        return output_dir / "analysis.json"

    latest_dir = latest_checkpoint_dir(output_dir)
    analysis_path = latest_dir / "analysis.json"
    if not analysis_path.is_file():
        return None

    _copy_artifacts(latest_dir, output_dir)
    return output_dir / "analysis.json"


def load_checkpoint_analysis(
    output_dir: Path,
    checkpoint_id: str,
    repo_dir: Path | None = None,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]] | None:
    resolved_repo = _resolve_repo_dir(output_dir, repo_dir)
    if resolved_repo is not None:
        artifact_commit_sha = _resolve_ref(resolved_repo, f"{CHECKPOINT_ARTIFACT_REF_PREFIX}/{checkpoint_id}")
        if artifact_commit_sha is not None:
            data = _load_json_blob_from_commit(resolved_repo, artifact_commit_sha, Path("analysis.json"))
            if data is not None:
                try:
                    return parse_unified_analysis(data)
                except Exception as exc:
                    logger.warning("Failed to parse checkpoint analysis from ref %s: %s", checkpoint_id, exc)
                    return None

    analysis_path = checkpoint_dir(output_dir, checkpoint_id) / "analysis.json"
    if not analysis_path.is_file():
        return None

    try:
        with open(analysis_path, "r", encoding="utf-8") as handle:
            return parse_unified_analysis(json.load(handle))
    except Exception as exc:
        logger.warning("Failed to load checkpoint analysis from %s: %s", analysis_path, exc)
        return None


def save_checkpoint(repo_dir: Path, output_dir: Path, run_id: str) -> CheckpointMetadata:
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.is_file():
        raise FileNotFoundError(f"Cannot create checkpoint without analysis.json at {analysis_path}")

    previous = get_latest_checkpoint(repo_dir, output_dir)
    checkpoint_id = _generate_checkpoint_id()

    git_parent_sha = _resolve_ref(repo_dir, LATEST_CHECKPOINT_REF)
    metadata_parent_sha = git_parent_sha
    if metadata_parent_sha is None and previous is not None:
        metadata_parent_sha = previous.commit_sha
    commit_sha = _create_checkpoint_commit(repo_dir, git_parent_sha, checkpoint_id, output_dir)

    git_artifact_parent_sha = _resolve_ref(repo_dir, LATEST_CHECKPOINT_ARTIFACT_REF)
    metadata_artifact_parent_sha = git_artifact_parent_sha
    if metadata_artifact_parent_sha is None and previous is not None:
        metadata_artifact_parent_sha = previous.artifact_commit_sha

    metadata = CheckpointMetadata(
        checkpoint_id=checkpoint_id,
        commit_sha=commit_sha,
        parent_commit_sha=metadata_parent_sha,
        created_at=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        parent_artifact_commit_sha=metadata_artifact_parent_sha,
    )
    artifact_commit_sha = _create_artifact_commit(repo_dir, git_artifact_parent_sha, metadata, output_dir)
    metadata = CheckpointMetadata(
        checkpoint_id=metadata.checkpoint_id,
        commit_sha=metadata.commit_sha,
        parent_commit_sha=metadata.parent_commit_sha,
        created_at=metadata.created_at,
        run_id=metadata.run_id,
        version=metadata.version,
        artifact_commit_sha=artifact_commit_sha,
        parent_artifact_commit_sha=metadata_artifact_parent_sha,
    )
    _update_refs(repo_dir, metadata)
    _persist_checkpoint_to_disk(metadata, output_dir)
    logger.info("Saved checkpoint %s at source=%s artifacts=%s", checkpoint_id, commit_sha, artifact_commit_sha)
    return metadata


def materialize_checkpoint_worktree(
    repo_dir: Path,
    output_dir: Path,
    checkpoint_id: str,
    worktree_path: Path | None = None,
) -> Path:
    metadata = load_checkpoint(output_dir, checkpoint_id, repo_dir)
    if metadata is None:
        raise FileNotFoundError(f"Unknown checkpoint_id={checkpoint_id}")

    target = worktree_path or (checkpoint_dir(output_dir, checkpoint_id) / "repo")
    if target.exists():
        return target

    repo = Repo(repo_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    repo.git.worktree("add", "--detach", str(target), metadata.commit_sha)
    return target


def _generate_checkpoint_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{uuid.uuid4().hex[:10]}"


def _copy_artifacts(source_dir: Path, destination_dir: Path) -> None:
    for filename in ARTIFACT_FILENAMES:
        source = source_dir / filename
        if source.is_file():
            shutil.copy2(source, destination_dir / filename)

    health_source = source_dir / HEALTH_ARTIFACT_PATH
    if health_source.is_file():
        health_destination = destination_dir / HEALTH_ARTIFACT_PATH
        health_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(health_source, health_destination)


def _load_metadata(path: Path) -> CheckpointMetadata | None:
    if not path.is_file():
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load checkpoint metadata from %s: %s", path, exc)
        return None

    return _metadata_from_dict(data)


def _metadata_from_dict(
    data: dict,
    *,
    artifact_commit_sha: str | None = None,
    parent_artifact_commit_sha: str | None = None,
) -> CheckpointMetadata | None:
    try:
        return CheckpointMetadata(
            checkpoint_id=data["checkpoint_id"],
            commit_sha=data["commit_sha"],
            parent_commit_sha=data.get("parent_commit_sha"),
            created_at=data["created_at"],
            run_id=data["run_id"],
            version=data.get("version", 1),
            artifact_commit_sha=artifact_commit_sha or data.get("artifact_commit_sha"),
            parent_artifact_commit_sha=parent_artifact_commit_sha or data.get("parent_artifact_commit_sha"),
        )
    except KeyError as exc:
        logger.warning("Invalid checkpoint metadata: missing %s", exc)
        return None


def _write_metadata(path: Path, metadata: CheckpointMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(asdict(metadata), handle, indent=2, sort_keys=True)


def _update_refs(repo_dir: Path, metadata: CheckpointMetadata) -> None:
    repo = Repo(repo_dir)
    repo.git.update_ref(metadata.checkpoint_ref, metadata.commit_sha)
    repo.git.update_ref(LATEST_CHECKPOINT_REF, metadata.commit_sha)
    if metadata.artifact_commit_sha is None:
        raise ValueError("artifact_commit_sha must be set before updating checkpoint refs")
    repo.git.update_ref(metadata.artifact_checkpoint_ref, metadata.artifact_commit_sha)
    repo.git.update_ref(LATEST_CHECKPOINT_ARTIFACT_REF, metadata.artifact_commit_sha)


def _create_checkpoint_commit(
    repo_dir: Path,
    parent_commit_sha: str | None,
    checkpoint_id: str,
    output_dir: Path,
) -> str:
    repo = Repo(repo_dir)
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
        with repo.git.custom_environment(**env):
            repo.git.add(A=True)

            output_rel = _relative_to_repo(repo_dir, output_dir)
            if output_rel is not None:
                repo.git.rm("-r", "--cached", "--ignore-unmatch", output_rel)

            tree_sha = repo.git.write_tree().strip()
            commit_args: list[str] = []
            if parent_commit_sha:
                commit_args.extend(["-p", parent_commit_sha])
            return repo.git.commit_tree(tree_sha, *commit_args, m=f"CodeBoarding checkpoint {checkpoint_id}").strip()
    finally:
        index_path.unlink(missing_ok=True)


def _create_artifact_commit(
    repo_dir: Path,
    parent_commit_sha: str | None,
    metadata: CheckpointMetadata,
    output_dir: Path,
) -> str:
    repo = Repo(repo_dir)
    fd, index_tmp = tempfile.mkstemp(prefix="codeboarding_artifact_index_")
    os.close(fd)
    index_path = Path(index_tmp)
    index_path.unlink(missing_ok=True)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(index_path)
    env.setdefault("GIT_AUTHOR_NAME", "CodeBoarding")
    env.setdefault("GIT_AUTHOR_EMAIL", "codeboarding@local")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])

    metadata_temp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="codeboarding_checkpoint_metadata_", delete=False
    )
    metadata_temp_path = Path(metadata_temp.name)
    try:
        json.dump(_checkpoint_metadata_payload(metadata), metadata_temp, indent=2, sort_keys=True)
        metadata_temp.close()

        with repo.git.custom_environment(**env):
            repo.git.read_tree("--empty")

            for artifact_path, source in _iter_artifact_sources(output_dir):
                blob_sha = repo.git.hash_object("-w", str(source)).strip()
                repo.git.update_index("--add", "--cacheinfo", "100644", blob_sha, artifact_path)

            metadata_blob_sha = repo.git.hash_object("-w", str(metadata_temp_path)).strip()
            repo.git.update_index("--add", "--cacheinfo", "100644", metadata_blob_sha, METADATA_FILENAME)

            tree_sha = repo.git.write_tree().strip()
            commit_args: list[str] = []
            if parent_commit_sha:
                commit_args.extend(["-p", parent_commit_sha])
            return repo.git.commit_tree(
                tree_sha,
                *commit_args,
                m=f"CodeBoarding checkpoint artifacts {metadata.checkpoint_id}",
            ).strip()
    finally:
        metadata_temp_path.unlink(missing_ok=True)
        index_path.unlink(missing_ok=True)


def _relative_to_repo(repo_dir: Path, target: Path) -> str | None:
    try:
        return target.resolve().relative_to(repo_dir.resolve()).as_posix()
    except ValueError:
        return None


def _resolve_ref(repo_dir: Path, ref_name: str) -> str | None:
    if repo_dir is None:
        return None
    try:
        return Repo(repo_dir).commit(ref_name).hexsha
    except (BadName, GitCommandError, ValueError):
        return None


def _load_metadata_from_artifact_ref(repo_dir: Path, ref_name: str) -> CheckpointMetadata | None:
    artifact_commit_sha = _resolve_ref(repo_dir, ref_name)
    if artifact_commit_sha is None:
        return None

    data = _load_json_blob_from_commit(repo_dir, artifact_commit_sha, Path(METADATA_FILENAME))
    if data is None:
        return None

    try:
        artifact_commit = Repo(repo_dir).commit(artifact_commit_sha)
    except (BadName, GitCommandError, ValueError):
        return None

    parent_artifact_commit_sha = artifact_commit.parents[0].hexsha if artifact_commit.parents else None
    return _metadata_from_dict(
        data,
        artifact_commit_sha=artifact_commit_sha,
        parent_artifact_commit_sha=parent_artifact_commit_sha,
    )


def _load_json_blob_from_commit(repo_dir: Path, commit_sha: str, relative_path: Path) -> dict | None:
    try:
        raw = Repo(repo_dir).git.show(f"{commit_sha}:{relative_path.as_posix()}")
    except GitCommandError:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode %s from checkpoint commit %s: %s", relative_path, commit_sha, exc)
        return None


def _restore_artifacts_from_commit(output_dir: Path, repo_dir: Path, artifact_commit_sha: str) -> None:
    for relative_path in [Path(filename) for filename in ARTIFACT_FILENAMES] + [HEALTH_ARTIFACT_PATH]:
        try:
            raw = Repo(repo_dir).git.show(f"{artifact_commit_sha}:{relative_path.as_posix()}")
        except GitCommandError:
            continue

        target = output_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw, encoding="utf-8")


def _checkpoint_metadata_payload(metadata: CheckpointMetadata) -> dict:
    return {
        "checkpoint_id": metadata.checkpoint_id,
        "commit_sha": metadata.commit_sha,
        "parent_commit_sha": metadata.parent_commit_sha,
        "created_at": metadata.created_at,
        "run_id": metadata.run_id,
        "version": metadata.version,
    }


def _persist_checkpoint_to_disk(metadata: CheckpointMetadata, output_dir: Path) -> None:
    cp_dir = checkpoint_dir(output_dir, metadata.checkpoint_id)
    _write_metadata(cp_dir / METADATA_FILENAME, metadata)
    _copy_artifacts(output_dir, cp_dir)

    latest_dir = latest_checkpoint_dir(output_dir)
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    _write_metadata(latest_dir / METADATA_FILENAME, metadata)
    _copy_artifacts(output_dir, latest_dir)


def _iter_artifact_sources(output_dir: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for filename in ARTIFACT_FILENAMES:
        source = output_dir / filename
        if source.is_file():
            sources.append((filename, source))

    health_source = output_dir / HEALTH_ARTIFACT_PATH
    if health_source.is_file():
        sources.append((HEALTH_ARTIFACT_PATH.as_posix(), health_source))

    return sources


def _resolve_repo_dir(output_dir: Path, repo_dir: Path | None = None) -> Path | None:
    if repo_dir is not None:
        return repo_dir

    candidate = output_dir.parent
    try:
        repo = Repo(candidate)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None

    working_tree_dir = repo.working_tree_dir
    if working_tree_dir is None:
        return None
    if Path(working_tree_dir).resolve() != candidate.resolve():
        return None
    return candidate
