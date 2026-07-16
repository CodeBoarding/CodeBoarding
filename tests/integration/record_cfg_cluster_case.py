"""Record canonical ProgramGraph fixtures for two commits of a repository.

Example:
    uv run python tests/integration/record_cfg_cluster_case.py \
      --repo /path/to/repo --base abc123 --head def456 --case add-routing
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import tempfile
from pathlib import Path

from static_analyzer import StaticAnalyzer
from utils import get_artifact_dir


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cfg_cluster_commit_pairs"


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _record(worktree: Path, commit: str) -> dict[str, dict]:
    _run_git(worktree, "checkout", "--detach", commit)
    resolved = _run_git(worktree, "rev-parse", "HEAD")
    with StaticAnalyzer(worktree) as analyzer:
        results = analyzer.analyze(
            cache_dir=get_artifact_dir(worktree),
            skip_cache=True,
            source_sha=resolved,
        )
    graphs: dict[str, dict] = {}
    for language in results.get_languages():
        graph = copy.deepcopy(results.get_program_graph(language))
        graph.visit_paths(lambda value: Path(value).resolve().relative_to(worktree.resolve()).as_posix())
        graphs[str(language)] = graph.to_dict()
    return graphs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="Local repository path or clone URL")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="codeboarding-cluster-case-") as temp:
        worktree = Path(temp) / "repo"
        source = Path(args.repo).expanduser()
        clone_source = str(source.resolve()) if source.exists() else args.repo
        subprocess.run(["git", "clone", clone_source, str(worktree)], check=True)
        base_sha = _run_git(worktree, "rev-parse", args.base)
        head_sha = _run_git(worktree, "rev-parse", args.head)
        base_graphs = _record(worktree, base_sha)
        head_graphs = _record(worktree, head_sha)

    case_dir = FIXTURE_ROOT / args.case
    case_dir.mkdir(parents=True, exist_ok=True)
    languages = sorted(set(base_graphs) | set(head_graphs))
    for language in languages:
        (case_dir / f"base-{language}.json").write_text(
            json.dumps(base_graphs.get(language, {"language": language, "nodes": [], "edges": []}), indent=2) + "\n"
        )
        (case_dir / f"head-{language}.json").write_text(
            json.dumps(head_graphs.get(language, {"language": language, "nodes": [], "edges": []}), indent=2) + "\n"
        )

    manifest = {
        "case": args.case,
        "repo": args.repo,
        "base": base_sha,
        "head": head_sha,
        "languages": {
            language: {
                "base_graph": f"base-{language}.json",
                "head_graph": f"head-{language}.json",
                "expected_graph_changes": {},
                "expected_clustering": {},
            }
            for language in languages
        },
    }
    (case_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(case_dir)


if __name__ == "__main__":
    main()
