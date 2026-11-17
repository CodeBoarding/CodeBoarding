"""Tests for the stored analysis.json layout: what it writes once, and what readers rebuild."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationCallSite,
    RelationEdge,
    SourceCodeReference,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from diagram_analysis import analysis_json
from diagram_analysis.analysis_json import (
    file_nested_relation_edges,
    _symbol_ids,
    build_unified_analysis_json,
    dump_analysis_document,
    parse_unified_analysis,
)
from diagram_analysis.exceptions import (
    AnalysisFormatError,
    AnalysisInvariantError,
    SymbolIdCollisionError,
    UnreadableAnalysisError,
)
from infra.analysis_format import ANALYSIS_FORMAT_VERSION

METHODS = {"a.py": ["a.one", "a.two"], "b.py": ["b.one"], "c.py": ["c.one"]}
Subs = dict[str, tuple[AnalysisInsights, list[Component]]]


def method(path: str, name: str) -> MethodEntry:
    line = METHODS[path].index(name) * 10 + 1
    return MethodEntry(qualified_name=name, start_line=line, end_line=line + 5, node_type="FUNCTION")


def ref(path: str, name: str) -> SourceCodeReference:
    if name not in METHODS.get(path, []):
        return SourceCodeReference(qualified_name=name, reference_file=path)
    entry = method(path, name)
    return SourceCodeReference(
        qualified_name=name,
        reference_file=path,
        reference_start_line=entry.start_line,
        reference_end_line=entry.end_line,
    )


def edge(source: str, target: str, *sites: tuple[int, int], description: str = "") -> RelationEdge:
    return RelationEdge(
        source=ref(*source.split("|")),
        target=ref(*target.split("|")),
        call_sites=[RelationCallSite(line=line, column=column) for line, column in sites],
        description=description,
    )


def relation(src_id: str, dst_id: str, all_edges: list[RelationEdge], key_edges=()) -> Relation:
    return Relation(
        relation="calls",
        src_name=f"c{src_id}",
        dst_name=f"c{dst_id}",
        src_id=src_id,
        dst_id=dst_id,
        is_static=True,
        all_edges=all_edges,
        key_edges=list(key_edges),
    )


def component(cid: str, members: dict[str, list[str]]) -> Component:
    return Component(
        name=f"c{cid}",
        description=f"component {cid}",
        key_entities=[],
        component_id=cid,
        file_methods=[
            FileMethodGroup(file_path=path, methods=[method(path, n) for n in names]) for path, names in members.items()
        ],
    )


def tree() -> tuple[AnalysisInsights, Subs]:
    """Parents '1' and '1.1', leaves '1.1.1', '1.1.2', '1.2' and '2', and one edge filed at each depth.

    a.one -> a.two parts inside '1.1'; a.two -> b.one parts inside '1', between a parent and a leaf;
    b.one -> c.one parts only at the top.
    """
    inside_one_one = edge("a.py|a.one", "a.py|a.two", (3, 4))
    inside_one = edge("a.py|a.two", "b.py|b.one", (7, 1), (9, 2), description="wires")
    across = edge("b.py|b.one", "c.py|c.one", (5, 5))
    root = AnalysisInsights(
        description="d",
        components=[
            component("1", {"a.py": ["a.one", "a.two"], "b.py": ["b.one"]}),
            component("2", {"c.py": ["c.one"]}),
        ],
        components_relations=[
            relation("1.1.1", "1.1.2", [inside_one_one], [inside_one_one.model_copy(update={"description": "loads"})]),
            relation("1.1.2", "1.2", [inside_one]),
            relation("1.2", "2", [across]),
        ],
    )
    root.files = {
        path: FileEntry(methods=[method(path, n) for n in names], content_hash=f"h{path}")
        for path, names in METHODS.items()
    }
    one = AnalysisInsights(
        description="component 1",
        components=[component("1.1", {"a.py": ["a.one", "a.two"]}), component("1.2", {"b.py": ["b.one"]})],
        components_relations=[relation("1.1", "1.2", [inside_one], [inside_one])],
    )
    one_one = AnalysisInsights(
        description="component 1.1",
        components=[component("1.1.1", {"a.py": ["a.one"]}), component("1.1.2", {"a.py": ["a.two"]})],
        components_relations=[relation("1.1.1", "1.1.2", [inside_one_one])],
    )
    return root, {"1": (one, []), "1.1": (one_one, [])}


def store(root: AnalysisInsights, subs: Subs) -> dict:
    expandable = [c for c in root.components if c.component_id in subs]
    return json.loads(
        build_unified_analysis_json(
            root, expandable, "repo", repo_dir=Path("/repo"), source_tree_hash="t", depth_cap=3, sub_analyses=subs
        )
    )


def resaved(document: dict) -> dict:
    """Read *document* and write it again; the generation time is the only field that may move."""
    parsed, parsed_subs = parse_unified_analysis(document)
    again = store(parsed, {cid: (sub, []) for cid, sub in parsed_subs.items()})
    again["metadata"]["generated_at"] = document["metadata"]["generated_at"]
    return again


class TestStoredShape(unittest.TestCase):
    def setUp(self):
        self.doc = store(*tree())
        self.ids = {f"{p}|{n}": row[4] for p, entry in self.doc["files"].items() for n, row in entry["methods"].items()}

    def test_methods_sit_under_their_file_as_rows(self):
        self.assertEqual(
            self.doc["files"]["a.py"],
            {
                "content_hash": "ha.py",
                "module_hash": "",
                "methods": {
                    "a.one": [1, 6, "FUNCTION", "", self.ids["a.py|a.one"]],
                    "a.two": [11, 16, "FUNCTION", "", self.ids["a.py|a.two"]],
                },
            },
        )
        self.assertEqual(self.doc["metadata"]["format_version"], ANALYSIS_FORMAT_VERSION)

    def test_each_edge_is_written_once_and_relations_cite_it(self):
        ids = self.ids
        self.assertEqual(
            self.doc["edges"],
            [
                [ids["a.py|a.one"], ids["a.py|a.two"], [[3, 4]]],
                [ids["a.py|a.two"], ids["b.py|b.one"], [[7, 1], [9, 2]], "wires"],
                [ids["b.py|b.one"], ids["c.py|c.one"], [[5, 5]]],
            ],
        )
        one = self.doc["components"][0]
        relations = [
            *self.doc["components_relations"],
            *one["components_relations"],
            *one["components"][0]["components_relations"],
        ]
        self.assertTrue(all("all_edges" not in relation for relation in relations))
        self.assertEqual(relations[0]["key_edges"], [[ids["a.py|a.one"], ids["a.py|a.two"], "loads"]])
        self.assertEqual(relations[3]["key_edges"], [[ids["a.py|a.two"], ids["b.py|b.one"], "wires"]])

    def test_only_leaves_list_their_members(self):
        one = self.doc["components"][0]

        self.assertNotIn("file_methods", one)
        self.assertNotIn("file_methods", one["components"][0])
        self.assertEqual(one["components"][0]["components"][0]["file_methods"], [self.ids["a.py|a.one"]])


class TestRoundTrip(unittest.TestCase):
    def test_parsing_recovers_every_relation_at_every_depth(self):
        root, subs = tree()

        parsed, parsed_subs = parse_unified_analysis(store(root, subs))

        self.assertEqual(parsed.components, root.components)
        self.assertEqual(parsed.components_relations, root.components_relations)
        for scope_id, (sub, _) in subs.items():
            self.assertEqual(parsed_subs[scope_id].components, sub.components)
            self.assertEqual(parsed_subs[scope_id].components_relations, sub.components_relations)
        self.assertEqual(
            {path: entry.methods for path, entry in parsed.files.items()},
            {path: entry.methods for path, entry in root.files.items()},
        )

    def test_saving_a_parsed_analysis_writes_the_same_document(self):
        stored = store(*tree())

        self.assertEqual(resaved(stored), stored)

    def test_a_file_path_containing_a_pipe_is_written_and_read(self):
        root = AnalysisInsights(description="d", components=[], components_relations=[])
        root.files = {
            "we|ird.py": FileEntry(
                methods=[MethodEntry(qualified_name="fn", start_line=1, end_line=2, node_type="FUNCTION")]
            )
        }

        parsed, _ = parse_unified_analysis(store(root, {}))

        self.assertEqual([m.qualified_name for m in parsed.files["we|ird.py"].methods], ["fn"])

    def test_methods_whose_joined_path_and_name_coincide_stay_apart(self):
        """Why: "a|b" + "c" and "a" + "b|c" both join to "a|b|c", but they are two methods."""
        root = AnalysisInsights(description="d", components=[], components_relations=[])
        root.files = {
            "a|b": FileEntry(methods=[MethodEntry(qualified_name="c", start_line=1, end_line=2, node_type="FUNCTION")]),
            "a": FileEntry(methods=[MethodEntry(qualified_name="b|c", start_line=3, end_line=4, node_type="FUNCTION")]),
        }

        doc = store(root, {})

        self.assertNotEqual(doc["files"]["a|b"]["methods"]["c"][4], doc["files"]["a"]["methods"]["b|c"][4])


class TestNestedRelationEdges(unittest.TestCase):
    def test_each_nested_relation_takes_the_top_level_edges_filed_under_it(self):
        root, subs = tree()
        expected = {cid: sub.components_relations for cid, (sub, _) in subs.items()}
        scopes = {cid: sub for cid, (sub, _) in subs.items()}
        for sub in scopes.values():
            sub.components_relations = [
                relation.model_copy(update={"all_edges": []}) for relation in sub.components_relations
            ]

        file_nested_relation_edges(root, scopes, Path("/repo"))

        self.assertEqual({cid: sub.components_relations for cid, sub in scopes.items()}, expected)


class TestAWriteThatWouldLoseFactsFails(unittest.TestCase):
    def test_a_top_level_relation_that_does_not_join_two_leaves(self):
        root, subs = tree()
        root.components_relations.append(relation("1", "2", [edge("b.py|b.one", "c.py|c.one", (5, 5))]))

        with self.assertRaisesRegex(AnalysisInvariantError, "relation 1 -> 2 holds 1 edge"):
            store(root, subs)

    def test_a_relation_holding_edges_its_scope_does_not_file_under_it(self):
        root, subs = tree()
        subs["1"][0].components_relations[0].all_edges = [edge("a.py|a.two", "b.py|b.one", (99, 1))]

        with self.assertRaisesRegex(AnalysisInvariantError, "relation 1.1 -> 1.2 holds"):
            store(root, subs)

    def test_a_key_edge_that_is_not_one_of_its_relations_edges(self):
        """Merging folds a relation's key edges into its edges, so one with other call sites is a second edge."""
        root, subs = tree()
        root.components_relations[0].key_edges = [edge("a.py|a.one", "a.py|a.two", (5, 5), description="loads")]

        with self.assertRaisesRegex(AnalysisInvariantError, "is held twice, differently"):
            store(root, subs)

    def test_a_parent_member_no_child_holds(self):
        root, subs = tree()
        root.components[0].file_methods.append(FileMethodGroup(file_path="c.py", methods=[method("c.py", "c.one")]))

        with self.assertRaisesRegex(AnalysisInvariantError, "Component 1 holds 1 method"):
            store(root, subs)

    def test_an_edge_to_a_method_no_file_indexes(self):
        root, subs = tree()
        root.components_relations[2].all_edges.append(edge("b.py|b.one", "c.py|vendor.absent", (8, 1)))

        with self.assertRaisesRegex(AnalysisInvariantError, "'vendor.absent'\\), which no file indexes"):
            store(root, subs)

    def test_a_method_two_leaves_hold(self):
        """Why: a reader files the method's edges under one of them, so the other's would be lost."""
        root, subs = tree()
        subs["1.1"][0].components[1].file_methods.append(
            FileMethodGroup(file_path="a.py", methods=[method("a.py", "a.one")])
        )

        with self.assertRaisesRegex(AnalysisInvariantError, "1 method\\(s\\) are members of more than one leaf"):
            store(root, subs)

    def test_a_component_twice_in_the_tree(self):
        root, subs = tree()
        subs["1.2"] = (
            AnalysisInsights(description="d", components=[component("1.1", {})], components_relations=[]),
            [],
        )

        with self.assertRaisesRegex(AnalysisInvariantError, "Component 1.1 appears twice"):
            store(root, subs)


def index(*keys: str) -> dict[str, FileEntry]:
    files: dict[str, FileEntry] = {}
    for key in keys:
        path, name = key.split("|")
        files.setdefault(path, FileEntry()).methods.append(
            MethodEntry(qualified_name=name, start_line=1, end_line=2, node_type="FUNCTION")
        )
    return files


class TestSymbolIds(unittest.TestCase):
    def test_ids_are_a_pure_function_of_the_method(self):
        alone = _symbol_ids(index("a.py|a.one"))
        among_others = _symbol_ids(index("0.py|z.one", "a.py|a.one", "b.py|b.one"))

        self.assertEqual(alone["a.py", "a.one"], among_others["a.py", "a.one"])
        self.assertRegex(alone["a.py", "a.one"], r"^[A-Za-z0-9_-]{12}$")

    def test_two_methods_sharing_an_id_fail_the_write(self):
        with patch.object(analysis_json, "_symbol_id", return_value="AAAAAAAAAAAA"):
            with self.assertRaises(SymbolIdCollisionError):
                store(*tree())


class TestRejectedDocuments(unittest.TestCase):
    def test_a_document_without_a_format_version_is_rejected(self):
        with self.assertRaisesRegex(AnalysisFormatError, "predates format versioning"):
            parse_unified_analysis({"metadata": {}, "methods_index": {}})

    def test_another_format_version_is_rejected(self):
        with self.assertRaisesRegex(AnalysisFormatError, "'v2'"):
            parse_unified_analysis({"metadata": {"format_version": "v2"}})

    def test_a_member_id_no_file_lists_is_unreadable(self):
        doc = store(*tree())
        doc["components"][1]["file_methods"][0] = "missing00000"

        with self.assertRaisesRegex(UnreadableAnalysisError, "'missing00000'"):
            parse_unified_analysis(doc)

    def test_an_edge_citing_an_id_no_file_lists_is_unreadable(self):
        doc = store(*tree())
        doc["edges"].append(["missing00000", doc["edges"][0][1], [[1, 1]]])

        with self.assertRaisesRegex(UnreadableAnalysisError, "'missing00000'"):
            parse_unified_analysis(doc)

    def test_a_key_edge_that_is_not_an_edge_is_unreadable(self):
        """Why: a KeyError here would fail the Action's run instead of sending it to a full one."""
        doc = store(*tree())
        doc["components_relations"][2]["key_edges"] = [doc["edges"][0][:2]]
        doc["edges"] = doc["edges"][1:]

        with self.assertRaisesRegex(UnreadableAnalysisError, "key edge .* that is not in edges"):
            parse_unified_analysis(doc)

    def test_a_document_that_is_not_an_object_is_unreadable(self):
        with self.assertRaisesRegex(UnreadableAnalysisError, "not a JSON object"):
            parse_unified_analysis([])  # type: ignore[arg-type]

    def test_a_document_without_metadata_is_unreadable(self):
        with self.assertRaisesRegex(UnreadableAnalysisError, "metadata object"):
            parse_unified_analysis({"metadata": None})

    def test_a_document_with_only_metadata_is_unreadable(self):
        with self.assertRaisesRegex(UnreadableAnalysisError, r"5 problem\(s\); at description: Field required"):
            parse_unified_analysis({"metadata": store(*tree())["metadata"]})

    def test_a_malformed_record_is_unreadable(self):
        doc = store(*tree())
        doc["edges"][0] = doc["edges"][0][:2]

        with self.assertRaisesRegex(UnreadableAnalysisError, "at edges.0"):
            parse_unified_analysis(doc)


class TestRecordWriter(unittest.TestCase):
    def test_short_records_sit_on_one_line_and_long_ones_break(self):
        text = dump_analysis_document({"short": [1, 2], "long": ["x" * 100, "y" * 100]})

        x, y = json.dumps("x" * 100), json.dumps("y" * 100)
        self.assertEqual(text, f'{{\n "short":[1,2],\n "long":[\n  {x},\n  {y}\n ]\n}}\n')

    def test_a_flat_record_and_its_comma_fit_the_width(self):
        lines = dump_analysis_document({"rows": [["x" * width] for width in range(150, 160)]}).splitlines()

        self.assertTrue(all(len(line) <= 160 for line in lines if line.startswith('  ["')))
        self.assertIn("  [", lines)


if __name__ == "__main__":
    unittest.main()
