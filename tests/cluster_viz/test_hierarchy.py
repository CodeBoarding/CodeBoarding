"""Tests for ``cluster_viz.hierarchy``: rebuilding scopes out of recorded cluster ids."""

import unittest

from cluster_viz.hierarchy import (
    build_scopes,
    flatten_components,
    is_cluster_id,
    level_of,
    lineage_path,
    path_conflicts,
    split_cluster_id,
)

_COMPONENTS = [
    {
        "component_id": "1",
        "name": "Engine",
        "description": "does things",
        "source_cluster_ids": ["1", "3"],
        "can_expand": True,
        "key_entities": [{"qualified_name": "pkg.Engine"}],
        "components": [
            {"component_id": "1.1", "name": "Parser", "source_cluster_ids": ["1.1", "1.4"], "components": []},
            {"component_id": "1.2", "name": "Writer", "source_cluster_ids": ["1.2"], "components": []},
        ],
    },
    {"component_id": "2", "name": "CLI", "source_cluster_ids": ["2"], "components": []},
]


class TestClusterIds(unittest.TestCase):
    def test_level_and_split(self):
        self.assertEqual(level_of("1"), 1)
        self.assertEqual(level_of("1.1.3"), 3)
        self.assertEqual(split_cluster_id("1.1.3"), ("1.1", "3"))
        self.assertEqual(split_cluster_id("7"), ("", "7"))

    def test_component_ids_are_not_cluster_ids(self):
        self.assertTrue(is_cluster_id("1.4"))
        self.assertFalse(is_cluster_id("1.x"))


class TestFlattenComponents(unittest.TestCase):
    def test_parents_levels_and_entities(self):
        flat = flatten_components(_COMPONENTS)

        self.assertEqual(sorted(flat), ["1", "1.1", "1.2", "2"])
        self.assertEqual(flat["1.1"].parent_id, "1")
        self.assertEqual(flat["1.1"].level, 2)
        self.assertEqual(flat["1"].key_entities, ["pkg.Engine"])
        self.assertEqual(flat["2"].description, "")


class TestBuildScopes(unittest.TestCase):
    def setUp(self):
        self.lineage = {
            "pkg.a": {"1", "1.1"},
            "pkg.b": {"1", "1.4"},
            "pkg.c": {"3", "1.2"},
            "pkg.d": {"2"},
        }
        self.scopes = build_scopes(self.lineage, flatten_components(_COMPONENTS))

    def test_scopes_split_by_owning_component(self):
        self.assertEqual(sorted(self.scopes), ["", "1"])
        self.assertEqual(sorted(self.scopes[""].clusters), ["1", "2", "3"])
        self.assertEqual(sorted(self.scopes["1"].clusters), ["1.1", "1.2", "1.4"])
        self.assertEqual(self.scopes["1"].level, 2)

    def test_groups_map_clusters_to_child_components(self):
        self.assertEqual(self.scopes[""].groups, {"1": ["1", "3"], "2": ["2"]})
        self.assertEqual(self.scopes["1"].cluster_owner(), {"1.1": "1.1", "1.4": "1.1", "1.2": "1.2"})

    def test_members_span_every_cluster(self):
        self.assertEqual(self.scopes[""].members(), {"pkg.a", "pkg.b", "pkg.c", "pkg.d"})


class TestLineagePath(unittest.TestCase):
    def test_path_is_indexed_by_level_and_padded(self):
        self.assertEqual(lineage_path({"1", "1.4", "1.1.2"}, 4), ["1", "1.4", "1.1.2", ""])

    def test_conflicting_levels_are_reported_and_resolved_deterministically(self):
        cluster_ids = {"1", "2", "1.4"}
        self.assertEqual(path_conflicts(cluster_ids), [1])
        self.assertEqual(lineage_path(cluster_ids, 2), ["1", "1.4"])

    def test_component_ids_are_ignored(self):
        self.assertEqual(path_conflicts({"1", "1.x"}), [])
