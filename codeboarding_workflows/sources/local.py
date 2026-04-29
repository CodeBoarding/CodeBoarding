from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SourceContext:
    """Materialized repo handed to a scope workflow.

    ``artifact_dir`` is where the workflow should write analysis outputs.
    For local sources it equals the caller-provided output dir; for remote
    sources it is a temp folder whose contents are copied out on exit.
    """

    repo_path: Path
    project_name: str
    artifact_dir: Path


@contextmanager
def local_source(repo_path: Path, project_name: str, artifact_dir: Path) -> Iterator[SourceContext]:
    yield SourceContext(repo_path=repo_path, project_name=project_name, artifact_dir=artifact_dir)
