"""Tests for ``build_prior_index`` — extracts prior cluster names + members
from a live ``analysis.json`` tree."""

import unittest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
)
from agents.prior_index_builder import build_prior_index


def _component(
    name: str,
    component_id: str,
    methods_by_file: dict[str, list[str]] | None = None,
) -> Component:
    file_methods = []
    for fp, qnames in (methods_by_file or {}).items():
        file_methods.append(
            FileMethodGroup(
                file_path=fp,
                methods=[MethodEntry(qualified_name=q, start_line=1, end_line=1, node_type="FUNCTION") for q in qnames],
            )
        )
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
        component_id=component_id,
        file_methods=file_methods,
    )


class TestBuildPriorIndex(unittest.TestCase):
    def test_empty_tree_yields_empty_index(self) -> None:
        root = AnalysisInsights(description="empty", components=[], components_relations=[])
        index = build_prior_index(root, {})
        self.assertIsNone(index.find_best_match(["any"]))

    def test_single_root_component_indexed(self) -> None:
        comp = _component("Auth", "1", {"auth.py": ["auth.login", "auth.logout"]})
        root = AnalysisInsights(description="r", components=[comp], components_relations=[])
        index = build_prior_index(root, {})
        match = index.find_best_match(["auth.login", "auth.logout"])
        self.assertIsNotNone(match)
        prior, score = match  # type: ignore[misc]
        self.assertEqual(prior.name, "Auth")
        self.assertEqual(score, 1.0)

    def test_sub_analyses_included(self) -> None:
        # Why this matters: DetailsAgent recurses through depth_level, and the
        # arbiter must apply at every layer. If sub-analysis components are
        # missed, deeper sub-clusters can never NOOP.
        root_comp = _component("Root", "1", {"r.py": ["root.fn"]})
        sub_comp = _component("Sub", "1.1", {"s.py": ["sub.fn"]})
        root = AnalysisInsights(description="r", components=[root_comp], components_relations=[])
        sub = AnalysisInsights(description="s", components=[sub_comp], components_relations=[])
        index = build_prior_index(root, {"1": sub})
        match = index.find_best_match(["sub.fn"])
        self.assertIsNotNone(match)
        prior, _ = match  # type: ignore[misc]
        self.assertEqual(prior.name, "Sub")

    def test_components_without_name_skipped(self) -> None:
        # Why skip nameless: an empty name is useless as a NOOP target.
        nameless = _component("", "1", {"f.py": ["fn"]})
        named = _component("Real", "2", {"f.py": ["fn"]})
        root = AnalysisInsights(description="r", components=[nameless, named], components_relations=[])
        index = build_prior_index(root, {})
        match = index.find_best_match(["fn"])
        self.assertIsNotNone(match)
        prior, _ = match  # type: ignore[misc]
        self.assertEqual(prior.name, "Real")

    def test_components_without_methods_skipped(self) -> None:
        # Why skip method-less: nothing to compute Jaccard against.
        empty = _component("EmptyComp", "1", {})
        full = _component("FullComp", "2", {"f.py": ["fn"]})
        root = AnalysisInsights(description="r", components=[empty, full], components_relations=[])
        index = build_prior_index(root, {})
        match = index.find_best_match(["fn"])
        self.assertIsNotNone(match)
        prior, _ = match  # type: ignore[misc]
        self.assertEqual(prior.name, "FullComp")

    def test_methods_flattened_across_multiple_files(self) -> None:
        comp = _component(
            "Multi",
            "1",
            {
                "a.py": ["pkg.a.fn1", "pkg.a.fn2"],
                "b.py": ["pkg.b.fn3"],
            },
        )
        root = AnalysisInsights(description="r", components=[comp], components_relations=[])
        index = build_prior_index(root, {})
        match = index.find_best_match(["pkg.a.fn1", "pkg.a.fn2", "pkg.b.fn3"])
        self.assertIsNotNone(match)
        prior, score = match  # type: ignore[misc]
        self.assertEqual(prior.name, "Multi")
        self.assertEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
