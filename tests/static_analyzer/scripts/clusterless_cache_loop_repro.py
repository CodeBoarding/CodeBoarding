"""Temporary reproducer for the persistent clusterless-cache failure loop."""

from pathlib import Path
from unittest.mock import patch

from static_analyzer import StaticAnalyzer
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterResult
from static_analyzer.clustering.snapshot import snapshot_from_static_analysis
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node
from static_analyzer.scanner import ProjectScanner


def test_incomplete_analyzer_attempts_do_not_replace_clustered_cache(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    artifact_dir = repo_dir / ".codeboarding"
    source_file = repo_dir / "app.py"
    repo_dir.mkdir()
    source_file.write_text("def run():\n    return 1\n")

    cache = StaticAnalysisCache(artifact_dir, repo_dir)
    clustered = _clusterless_static_analysis(source_file)
    clustered.get_clusters(Language.PYTHON).record_scope(ClusterResult(clusters={7: {"app.run"}}))
    cache.save(clustered, source_sha="old-source")
    cache.sha_path.write_text("v4\nold-source\n")
    assert cache.load_with_sha() is None
    original_pkl = cache.pkl_path.read_bytes()
    original_tag = cache.sha_path.read_bytes()

    for attempt in range(1, 4):
        incomplete = _run_incomplete_full_pass(repo_dir, artifact_dir, source_file, attempt)
        assert snapshot_from_static_analysis(incomplete).all_cluster_ids() == set()
        assert cache.pkl_path.read_bytes() == original_pkl
        assert cache.sha_path.read_bytes() == original_tag


def _clusterless_static_analysis(source_file: Path) -> StaticAnalysisResults:
    graph = CallGraph(language="python")
    graph.add_node(
        Node(
            fully_qualified_name="app.run",
            node_type=NodeType.FUNCTION,
            file_path=str(source_file),
            line_start=1,
            line_end=2,
        )
    )
    results = StaticAnalysisResults()
    results.add_cfg(Language.PYTHON, graph)
    results.add_source_files(Language.PYTHON, [str(source_file)])
    return results


def _run_incomplete_full_pass(
    repo_dir: Path,
    artifact_dir: Path,
    source_file: Path,
    attempt: int,
) -> StaticAnalysisResults:
    with patch.object(ProjectScanner, "scan", return_value=[]):
        analyzer = StaticAnalyzer(repo_dir, changed_files=set())

    with patch.object(analyzer, "_run_full_lsp_pass", return_value=_clusterless_static_analysis(source_file)):
        with analyzer:
            return analyzer.analyze(
                cache_dir=artifact_dir,
                skip_cache=True,
                source_sha=f"incremental-attempt-{attempt}",
            )
