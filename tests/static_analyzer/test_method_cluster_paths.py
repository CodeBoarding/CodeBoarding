import pickle

from static_analyzer.clustering import ClusterResult
from static_analyzer.method_cluster_paths import MethodClusterPaths


def test_scoped_high_watermark_survives_pruning_and_pickle() -> None:
    paths = MethodClusterPaths()
    paths.record(ClusterResult(clusters={0: {"a"}, 2: {"b"}}), "1")

    pruned = paths.prune({})
    restored = pickle.loads(pickle.dumps(pruned))

    assert restored.snapshot() == []
    assert restored.next_cluster_id("1") == 3


def test_legacy_path_only_pickle_state_derives_high_watermark() -> None:
    paths = MethodClusterPaths()
    paths.__setstate__({"a": {"1.4"}})

    assert paths.next_cluster_id("1") == 5
