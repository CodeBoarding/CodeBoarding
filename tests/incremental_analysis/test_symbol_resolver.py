"""Regression tests for the incremental-side symbol resolver.

Locks the contract that ``StaticAnalysisSymbolResolver`` returns the
same canonical method set the persisted baseline holds, so a
comments-only edit against a baseline-loaded ``FileEntry.methods`` does
not produce phantom add/delete pairs.

The resolver was previously built from ``iter_reference_nodes`` and
filtered ``is_callback_or_anonymous`` — both choices diverged from the
full-analysis projection that wrote the baseline, fabricating fake
deletions on every refresh. These tests assert the new CFG-based
projection matches.
"""

import unittest
from pathlib import Path

from incremental_analysis.symbol_resolver import StaticAnalysisSymbolResolver
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


class TestStaticAnalysisSymbolResolver(unittest.TestCase):
    def _make_static(self, language: str, nodes: list[Node]) -> StaticAnalysisResults:
        cfg = CallGraph(language=language)
        for node in nodes:
            cfg.add_node(node)
        sa = StaticAnalysisResults()
        sa.add_cfg(language, cfg)
        return sa

    def test_resolves_callable_class_nodes_from_cfg(self) -> None:
        repo = Path("/repo")
        sa = self._make_static(
            "typescript",
            [
                Node("a.b.foo", NodeType.FUNCTION, "/repo/a/b.ts", 1, 5),
                Node("a.b.Bar", NodeType.CLASS, "/repo/a/b.ts", 7, 20),
                Node("a.b.IGNORED", NodeType.VARIABLE, "/repo/a/b.ts", 22, 22),
            ],
        )
        resolver = StaticAnalysisSymbolResolver(sa, repo)

        methods = resolver.resolve("a/b.ts")
        qnames = sorted(m.qualified_name for m in methods)
        self.assertEqual(qnames, ["a.b.Bar", "a.b.foo"])

    def test_keeps_anonymous_callbacks(self) -> None:
        """The previous resolver dropped ``is_callback_or_anonymous`` nodes,
        which made the diff fabricate ``deleted_methods`` whenever the
        baseline contained any. The new contract: keep them, dedupe via
        the shared projection."""
        repo = Path("/repo")
        sa = self._make_static(
            "typescript",
            [
                Node("a.b.handler", NodeType.FUNCTION, "/repo/a/b.ts", 10, 20),
                Node("a.b.handler.useEffect() callback", NodeType.FUNCTION, "/repo/a/b.ts", 12, 18),
            ],
        )
        resolver = StaticAnalysisSymbolResolver(sa, repo)
        qnames = sorted(m.qualified_name for m in resolver.resolve("a/b.ts"))
        self.assertEqual(qnames, ["a.b.handler", "a.b.handler.useEffect() callback"])

    def test_returns_empty_for_unknown_file(self) -> None:
        sa = self._make_static("typescript", [Node("a.b.foo", NodeType.FUNCTION, "/repo/a/b.ts", 1, 5)])
        resolver = StaticAnalysisSymbolResolver(sa, Path("/repo"))
        self.assertEqual(resolver.resolve("does/not/exist.ts"), [])

    def test_lookup_normalizes_absolute_paths(self) -> None:
        repo = Path("/repo")
        sa = self._make_static("typescript", [Node("a.b.foo", NodeType.FUNCTION, "/repo/a/b.ts", 1, 5)])
        resolver = StaticAnalysisSymbolResolver(sa, repo)
        # Same file path — given absolute or relative — should resolve identically.
        self.assertEqual(
            [m.qualified_name for m in resolver.resolve("/repo/a/b.ts")],
            [m.qualified_name for m in resolver.resolve("a/b.ts")],
        )

    def test_baseline_qname_set_matches_resolver_for_unchanged_file(self) -> None:
        """End-to-end contract: the qname set ``parser.py`` reconstructs
        from ``methods_index`` for a baseline must match what the
        resolver returns when nothing changed. This is the property
        ``IncrementalUpdater._compute_file_delta`` relies on to keep
        ``deleted_methods`` empty for cosmetic edits.

        We simulate the baseline as the canonical projection of the
        same CFG; then assert the resolver returns the exact same set.
        """
        repo = Path("/repo")
        nodes = [
            Node("a.b.foo", NodeType.FUNCTION, "/repo/a/b.ts", 1, 5),
            Node("a.b.Bar", NodeType.CLASS, "/repo/a/b.ts", 7, 20),
            Node("a.b.Bar.useEffect() callback", NodeType.FUNCTION, "/repo/a/b.ts", 12, 18),
            Node("a.b.Bar.method", NodeType.METHOD, "/repo/a/b.ts", 8, 11),
        ]
        sa = self._make_static("typescript", nodes)
        resolver = StaticAnalysisSymbolResolver(sa, repo)

        # The "baseline" is whatever full analysis would have persisted
        # for the same nodes — i.e. the same canonical projection.
        from agents.method_projection import build_file_method_groups

        baseline_groups = build_file_method_groups(nodes, repo)
        baseline_qnames = sorted(m.qualified_name for g in baseline_groups for m in g.methods)

        resolver_qnames = sorted(m.qualified_name for m in resolver.resolve("a/b.ts"))
        self.assertEqual(baseline_qnames, resolver_qnames)


if __name__ == "__main__":
    unittest.main()
