"""Regression tests for the canonical method projection helper.

This is the shared "what methods exist in a file" projection used by
both full analysis and the incremental resolver. The same dedupe rule
the existing ``test_cluster_methods_mixin.py::TestBuildFileMethodsFromNodes``
test asserts is reproduced here as a free-standing function test, plus
an explicit assertion that anonymous-callback nodes are *not* filtered
out (that filter would re-create the bug we extracted this helper to
fix — incremental and full would disagree on which methods exist, and
the diff would fabricate add/delete pairs).
"""

import unittest
from pathlib import Path

from agents.method_projection import build_file_method_groups
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


class TestBuildFileMethodGroups(unittest.TestCase):
    def test_dedupe_keeps_more_specific_qname(self) -> None:
        repo_dir = Path("/repo")
        specific = Node(
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )
        alias = Node(
            "diagram_analysis.diagram_generator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )

        groups = build_file_method_groups([alias, specific], repo_dir)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].file_path, "diagram_analysis/diagram_generator.py")
        self.assertEqual(len(groups[0].methods), 1)
        self.assertEqual(
            groups[0].methods[0].qualified_name,
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
        )

    def test_dedupe_is_symmetric_regardless_of_input_order(self) -> None:
        repo_dir = Path("/repo")
        specific = Node("a.b.C.foo", NodeType.METHOD, "/repo/a/b.py", 1, 5)
        alias = Node("a.b.foo", NodeType.METHOD, "/repo/a/b.py", 1, 5)

        groups_ab = build_file_method_groups([alias, specific], repo_dir)
        groups_ba = build_file_method_groups([specific, alias], repo_dir)

        self.assertEqual(groups_ab[0].methods[0].qualified_name, "a.b.C.foo")
        self.assertEqual(groups_ba[0].methods[0].qualified_name, "a.b.C.foo")

    def test_callbacks_are_kept_not_filtered(self) -> None:
        """Anonymous callback nodes must survive projection.

        The persisted ``methods_index`` records callbacks; if the
        projection drops them on the resolver side, the diff fabricates
        ``deleted_methods`` entries on every refresh of any file with
        callbacks (see the FileSearch.tsx incident — five phantom
        deletions blocked the AST cosmetic fast path).
        """
        repo_dir = Path("/repo")
        named = Node("a.b.handler", NodeType.FUNCTION, "/repo/a/b.ts", 10, 20)
        callback = Node(
            "a.b.handler.useEffect() callback",
            NodeType.FUNCTION,
            "/repo/a/b.ts",
            12,
            18,
        )

        groups = build_file_method_groups([named, callback], repo_dir)
        self.assertEqual(len(groups), 1)
        qnames = sorted(m.qualified_name for m in groups[0].methods)
        self.assertEqual(qnames, ["a.b.handler", "a.b.handler.useEffect() callback"])

    def test_excludes_non_callable_non_class_nodes(self) -> None:
        """Variables / properties are still excluded — only the callback filter changed."""
        repo_dir = Path("/repo")
        method = Node("a.b.foo", NodeType.METHOD, "/repo/a/b.py", 1, 5)
        variable = Node("a.b.SOME_CONSTANT", NodeType.VARIABLE, "/repo/a/b.py", 7, 7)

        groups = build_file_method_groups([method, variable], repo_dir)
        qnames = [m.qualified_name for m in groups[0].methods]
        self.assertEqual(qnames, ["a.b.foo"])


if __name__ == "__main__":
    unittest.main()
