"""A full run over a previous analysis keeps what that analysis said about the components it continues."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import Relation, RelationEdge, SourceCodeReference
from agents.scope_analysis_agent import ScopeAnalysisResult, ScopeComponentSemantics, ScopeRelationSemantics
from diagram_analysis.diagram_generator import DiagramGenerator, _ComponentBaseline, _MembershipBaseline
from diagram_analysis.io_utils import load_full_analysis
from infra.clustering_ids import ROOT_SCOPE_ID
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node

BILLING_RUN = "src.billing.billing_0.run_0"
ORDERS_RUN = "src.orders.orders_0.run_0"
SHIPPING_RUN = "src.shipping.shipping_0.run_0"
WORDS = ["Ledger", "Dispatch", "Pricing", "Routing", "Archive", "Audit", "Cache", "Gateway", "Inbox", "Journal"]


class NamingAgent:
    """Names every group it may edit ``<word> <run>``, so a carried name is told apart from a fresh one.

    It labels every connection it is shown with its calls as key edges, adds ``relations`` (source member, target
    member, label), and gives each group the key entities of its members named in ``key_entities``.
    """

    def __init__(self, run: str, relations: tuple[tuple[str, str, str], ...] = (), key_entities: tuple[str, ...] = ()):
        self.run = run
        self.relations = relations
        self.key_entities = key_entities
        self.calls: list[tuple[str, set[str], frozenset[str]]] = []
        self.changed_files: list[frozenset[str]] = []

    def analyze(self, scope, analysis, editable_group_ids, locked_name_ids, changed_files=frozenset(), *a, **kw):
        self.calls.append((scope.scope_id, set(editable_group_ids), frozenset(locked_name_ids)))
        self.changed_files.append(frozenset(changed_files))
        owner = {qname: group.group_id for group in scope.groups for qname in group.qualified_names}
        relations = [
            ScopeRelationSemantics(
                source_group_id=relation.src_id,
                target_group_id=relation.dst_id,
                relation=f"calls ({self.run})",
                key_edges=[edge.model_copy(deep=True) for edge in relation.all_edges],
            )
            for relation in analysis.components_relations
        ] + [
            ScopeRelationSemantics(
                source_group_id=owner[source],
                target_group_id=owner[target],
                relation=label,
                evidence=f"{label} in settings",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(qualified_name=source),
                        target=SourceCodeReference(qualified_name=target),
                    )
                ],
            )
            for source, target, label in self.relations
            if source in owner and target in owner
        ]
        return ScopeAnalysisResult(
            description=f"Scope {scope.scope_id} as of {self.run}",
            components=[
                ScopeComponentSemantics(
                    group_id=group_id,
                    name=f"{WORDS[index % len(WORDS)]} {self.run} handling",
                    description=f"{group_id} described in {self.run}",
                    # A model that names no key entity leaves the key out, which keeps the ones the group had.
                    **(
                        {"key_entities": [SourceCodeReference(qualified_name=qname) for qname in owned]}
                        if (owned := [qname for qname in self.key_entities if owner.get(qname) == group_id])
                        else {}
                    ),
                )
                for index, group_id in enumerate(sorted(editable_group_ids))
            ],
            relations=relations,
        )


class TestFullRerunKeepsNames(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.output = self.repo / ".codeboarding"
        self.packages = {"billing": 6, "orders": 5, "shipping": 4}

    def tearDown(self):
        self.temp.cleanup()

    def _write(
        self, packages: dict[str, int], edited: str = "", calls: tuple[tuple[str, str], ...] = ()
    ) -> StaticAnalysisResults:
        graph = CallGraph(language="python")
        files: list[str] = []
        for package, count in packages.items():
            for index in range(count):
                path = self.repo / "src" / package / f"{package}_{index}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                body = "1" if str(path.relative_to(self.repo)) != edited else "2"
                path.write_text(f"def run_{index}():\n    return {body}\n\n\ndef stop_{index}():\n    return 0\n")
                files.append(str(path))
                module = f"src.{package}.{package}_{index}"
                graph.add_node(Node(f"{module}.run_{index}", NodeType.FUNCTION, str(path), 1, 2))
                graph.add_node(Node(f"{module}.stop_{index}", NodeType.FUNCTION, str(path), 5, 6))
        for source, target in calls:
            graph.add_edge(source, target)
        results = StaticAnalysisResults()
        results.add_cfg(Language.PYTHON, graph)
        results.add_references(Language.PYTHON, list(graph.nodes.values()))
        results.add_source_files(Language.PYTHON, files)
        return results

    def _owner(self, package: str) -> str:
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        return next(
            component.component_id
            for component in loaded[0].components
            if any(group.file_path.startswith(f"src/{package}/") for group in component.file_methods)
        )

    def _full_run(
        self, results: StaticAnalysisResults, run: str, agent: NamingAgent | None = None
    ) -> tuple[NamingAgent, dict[str, tuple[str, str]]]:
        analyzer = MagicMock(collected_diagnostics={})
        analyzer.analyze.return_value = results
        generator = DiagramGenerator(
            repo_location=self.repo,
            temp_folder=Path(self.temp.name),
            repo_name="repo",
            output_dir=self.output,
            depth_cap=1,
            run_id=run,
            log_path=f"repo/{run}",
            static_analyzer=analyzer,
        )
        agent = agent or NamingAgent(run)
        generator.scope_analysis_agent = agent
        generator._agent_llm = MagicMock()
        with patch("diagram_analysis.diagram_generator.ProjectScanner.scan", return_value=[]):
            generator.generate_analysis()
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        root, _subs = loaded
        return agent, {c.component_id: (c.name, c.description) for c in root.components}

    def _relations(self) -> dict[tuple[str, str], Relation]:
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        return {(relation.src_id, relation.dst_id): relation for relation in loaded[0].components_relations}

    def test_a_rerun_over_unchanged_code_asks_the_model_nothing_and_changes_nothing(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages), "second")
        self.assertGreater(len(first), 1)
        self.assertEqual(second, first)
        self.assertEqual(agent.calls, [])

    def test_a_component_whose_file_changed_keeps_its_name_and_is_described_again(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages, edited="src/orders/orders_0.py"), "second")
        [(scope_id, editable, locked)] = agent.calls
        self.assertEqual(scope_id, ROOT_SCOPE_ID)
        self.assertEqual(len(editable), 1)
        self.assertEqual(locked, frozenset(editable))
        [changed] = editable
        self.assertEqual(second[changed][0], first[changed][0])
        self.assertIn("second", second[changed][1])
        self.assertEqual(
            {key: value for key, value in second.items() if key != changed},
            {key: value for key, value in first.items() if key != changed},
        )

    def test_ids_follow_the_code_when_the_size_order_changes(self):
        """Shipping grows from the smallest package to the largest; 4 of its 7 files are the old ones."""
        _, first = self._full_run(self._write(self.packages), "first")
        before = {package: self._owner(package) for package in self.packages}
        agent, second = self._full_run(self._write(self.packages | {"shipping": 7}), "second")
        self.assertEqual({package: self._owner(package) for package in self.packages}, before)
        [(_, editable, locked)] = agent.calls
        self.assertEqual(editable, {before["shipping"]})
        self.assertEqual(locked, frozenset(), "4 of 7 files is too little of the old component to lock its name")
        for package in ("billing", "orders"):
            self.assertEqual(second[before[package]], first[before[package]])

    def test_a_new_package_gets_a_fresh_id_and_a_fresh_name(self):
        _, first = self._full_run(self._write(self.packages), "first")
        agent, second = self._full_run(self._write(self.packages | {"returns": 5}), "second")
        [new_id] = second.keys() - first.keys()
        self.assertEqual(int(new_id), max(int(component_id) for component_id in first) + 1)
        self.assertIn("second", second[new_id][0])
        self.assertTrue(all(second[component_id] == first[component_id] for component_id in first))

    def test_no_previous_analysis_names_everything(self):
        agent, first = self._full_run(self._write(self.packages), "first")
        [(scope_id, editable, locked)] = agent.calls
        self.assertEqual(editable, set(first))
        self.assertEqual(locked, frozenset())

    def test_a_call_gone_from_the_graph_takes_its_carried_relation_with_it(self):
        self._full_run(self._write(self.packages, calls=((BILLING_RUN, ORDERS_RUN),)), "first")
        pair = (self._owner("billing"), self._owner("orders"))
        self.assertEqual(self._relations()[pair].relation, "calls (first)")
        agent, _ = self._full_run(self._write(self.packages), "second")
        self.assertNotIn(pair, self._relations())
        self.assertEqual(agent.calls, [])

    def test_a_carried_relation_keeps_only_the_calls_that_still_exist(self):
        kept, gone = (BILLING_RUN, ORDERS_RUN), ("src.billing.billing_1.run_1", "src.orders.orders_1.run_1")
        self._full_run(self._write(self.packages, calls=(kept, gone)), "first")
        pair = (self._owner("billing"), self._owner("orders"))
        self._full_run(self._write(self.packages, calls=(kept,)), "second")
        relation = self._relations()[pair]
        self.assertEqual(relation.relation, "calls (first)")
        edges = [(edge.source.qualified_name, edge.target.qualified_name) for edge in relation.key_edges]
        self.assertEqual(edges, [kept])

    def test_a_relation_on_evidence_alone_is_carried_while_its_symbols_resolve(self):
        configured = NamingAgent("first", relations=((BILLING_RUN, SHIPPING_RUN, "configures"),))
        self._full_run(self._write(self.packages), "first", configured)
        pair = (self._owner("billing"), self._owner("shipping"))
        self.assertEqual(self._relations()[pair].relation, "configures")
        agent, _ = self._full_run(self._write(self.packages), "second")
        self.assertEqual(self._relations()[pair].relation, "configures")
        self.assertEqual(agent.calls, [])

    def test_an_unreadable_previous_analysis_is_named_afresh_and_named_in_a_warning(self):
        self._full_run(self._write(self.packages), "first")
        path = self.output / "analysis.json"
        document = json.loads(path.read_text())
        document["metadata"]["tree_spec"] = {"scopes": 5}
        path.write_text(json.dumps(document))
        with self.assertLogs("diagram_analysis.diagram_generator", level="WARNING") as logs:
            _, second = self._full_run(self._write(self.packages), "second")
        self.assertTrue(any(str(self.output / "analysis.json") in line for line in logs.output))
        self.assertTrue(all("second" in name for name, _ in second.values()))

    def test_a_key_entity_follows_its_method_into_another_file(self):
        self._full_run(self._write(self.packages), "first", NamingAgent("first", key_entities=(BILLING_RUN,)))
        results = self._write(self.packages)
        target = self.repo / "src" / "billing" / "billing_1.py"
        target.write_text(target.read_text() + "\n\ndef run_0():\n    return 1\n")
        node = results.get_cfg(Language.PYTHON).nodes[BILLING_RUN]
        node.file_path, node.line_start, node.line_end = str(target), 9, 10
        self._full_run(results, "second")
        loaded = load_full_analysis(self.output)
        assert loaded is not None
        billing = next(c for c in loaded[0].components if c.component_id == self._owner("billing"))
        [entity] = billing.key_entities
        self.assertEqual((entity.qualified_name, Path(entity.reference_file or "").name), (BILLING_RUN, "billing_1.py"))

    def test_placeholders_of_an_analysis_saved_without_its_specification_are_named_again(self):
        silent = NamingAgent("first")
        silent.analyze = lambda *args, **kwargs: None
        _, first = self._full_run(self._write(self.packages), "first", silent)
        path = self.output / "analysis.json"
        document = json.loads(path.read_text())
        del document["metadata"]["tree_spec"]
        path.write_text(json.dumps(document))
        agent, second = self._full_run(self._write(self.packages), "second")
        self.assertEqual(agent.calls[0][1], set(first))
        self.assertTrue(all("second" in name for name, _ in second.values()))

    def test_a_new_file_reaches_the_model_as_changed(self):
        self._full_run(self._write(self.packages), "first")
        agent, _ = self._full_run(self._write(self.packages | {"billing": 7}), "second")
        [changed_files] = agent.changed_files
        self.assertIn("src/billing/billing_6.py", changed_files)


class TestCarriedSiblingNames(unittest.TestCase):
    def test_two_components_carried_from_different_scopes_do_not_share_a_name(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = DiagramGenerator(
                repo_location=Path(temp),
                temp_folder=Path(temp),
                repo_name="repo",
                output_dir=Path(temp) / ".codeboarding",
                depth_cap=1,
                run_id="run",
                log_path="repo/run",
            )
        graph = CallGraph(language="python")
        graph.add_node(Node("pkg.a", NodeType.FUNCTION, "a.py", 1, 2))
        graph.add_node(Node("pkg.b", NodeType.FUNCTION, "b.py", 1, 2))
        static_analysis = StaticAnalysisResults()
        static_analysis.add_cfg(Language.PYTHON, graph)
        generator.static_analysis = static_analysis
        scope = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            graphs_by_language={"python": graph},
            groups=[
                ClusterGroup(group_id="1", name="a", cluster_ids=[1], symbol_members_by_language={"python": {"pkg.a"}}),
                ClusterGroup(group_id="2", name="b", cluster_ids=[2], symbol_members_by_language={"python": {"pkg.b"}}),
            ],
        )
        generator._previous = _MembershipBaseline(
            meta_by_id={
                old_id: _ComponentBaseline(
                    name="Ledger handling",
                    description="",
                    key_entities=[],
                    source_cluster_ids=[],
                    member_keys=frozenset({(f"{member}.py", f"pkg.{member}")}),
                    member_qnames=frozenset({f"pkg.{member}"}),
                )
                for old_id, member in (("1.1", "a"), ("2.1", "b"))
            }
        )
        generator._carried_from = {"1": "1.1", "2": "2.1"}
        generator._enrich_scope = Mock()
        analysis = generator.scope_assembler.build(scope)

        generator._enrich_drafted_scope(scope, analysis)

        names = {component.component_id: component.name for component in analysis.components}
        self.assertEqual(names["1"], "Ledger handling")
        self.assertNotEqual(names["2"], "Ledger handling")
        self.assertEqual(generator._enrich_scope.call_args.args[2:4], ({"2"}, frozenset()))


if __name__ == "__main__":
    unittest.main()
