from static_analyzer.method_cluster_paths import MethodClusterPaths


def test_restore_re_adds_a_carried_over_methods_lost_lineage():
    # A method dropped and re-added by an incremental rebuild has no path; restore
    # gives it back the baseline's so its component is not re-seeded from nothing.
    paths = MethodClusterPaths({"pkg.kept": {"1.2"}})
    baseline = {"pkg.kept": {"1.2"}, "pkg.rebuilt": {"3.4"}}
    paths.restore(baseline, surviving={"pkg.kept", "pkg.rebuilt"})
    assert dict(paths.snapshot()) == {"pkg.kept": {"1.2"}, "pkg.rebuilt": {"3.4"}}


def test_restore_does_not_touch_a_method_that_already_has_a_path():
    # A method the current run already placed keeps its own path, not the stale one.
    paths = MethodClusterPaths({"pkg.moved": {"5.1"}})
    paths.restore({"pkg.moved": {"3.4"}}, surviving={"pkg.moved"})
    assert dict(paths.snapshot()) == {"pkg.moved": {"5.1"}}


def test_restore_skips_a_method_that_did_not_survive():
    paths = MethodClusterPaths()
    paths.restore({"pkg.gone": {"3.4"}}, surviving=set())
    assert paths.snapshot() == []
