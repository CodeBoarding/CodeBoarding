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
from agents.relation_edges import merge_relations_by_pair
from diagram_analysis import analysis_json
from diagram_analysis.analysis_json import (
    _symbol_ids,
    build_unified_analysis_json,
    dump_analysis_document,
    parse_unified_analysis,
)
from diagram_analysis.exceptions import AnalysisFormatError, SymbolIdCollisionError
from infra.analysis_format import ANALYSIS_FORMAT_VERSION

METHODS = {"a.py": ["a.one", "a.two"], "b.py": ["b.one"], "c.py": ["c.one"]}


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


def tree() -> tuple[AnalysisInsights, dict[str, tuple[AnalysisInsights, list[Component]]]]:
    """Root components '1' (leaves '1.1' and '1.2') and '2'; relations join leaves at the top."""
    one_to_b = edge("a.py|a.one", "b.py|b.one", (3, 4))
    two_to_c = edge("a.py|a.two", "c.py|c.one", (7, 1), (9, 2), description="wires")
    key = edge("a.py|a.one", "b.py|b.one", (3, 4), description="loads")
    root = AnalysisInsights(
        description="d",
        components=[
            component("1", {"a.py": ["a.one", "a.two"], "b.py": ["b.one"]}),
            component("2", {"c.py": ["c.one"]}),
        ],
        components_relations=[relation("1.1", "1.2", [one_to_b], [key]), relation("1.1", "2", [two_to_c])],
    )
    root.files = {
        path: FileEntry(methods=[method(path, n) for n in names], content_hash=f"h{path}")
        for path, names in METHODS.items()
    }
    sub = AnalysisInsights(
        description="component 1",
        components=[component("1.1", {"a.py": ["a.one", "a.two"]}), component("1.2", {"b.py": ["b.one"]})],
        components_relations=[relation("1.1", "1.2", [one_to_b], [key])],
    )
    return root, {"1": (sub, [])}


def store(root: AnalysisInsights, subs: dict[str, tuple[AnalysisInsights, list[Component]]]) -> dict:
    expandable = [c for c in root.components if c.component_id in subs]
    return json.loads(
        build_unified_analysis_json(
            root, expandable, "repo", repo_dir=Path("/repo"), source_tree_hash="t", depth_cap=2, sub_analyses=subs
        )
    )


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
        self.assertEqual(
            self.doc["edges"],
            [
                [self.ids["a.py|a.one"], self.ids["b.py|b.one"], [[3, 4]]],
                [self.ids["a.py|a.two"], self.ids["c.py|c.one"], [[7, 1], [9, 2]], "wires"],
            ],
        )
        top, nested = self.doc["components_relations"][0], self.doc["components"][0]["components_relations"][0]
        self.assertNotIn("all_edges", top)
        self.assertNotIn("all_edges", nested)
        self.assertEqual(top["key_edges"], [[self.ids["a.py|a.one"], self.ids["b.py|b.one"], "loads"]])

    def test_only_leaves_list_their_members(self):
        parent = self.doc["components"][0]

        self.assertNotIn("file_methods", parent)
        self.assertEqual(parent["components"][0]["file_methods"], [self.ids["a.py|a.one"], self.ids["a.py|a.two"]])


class TestRoundTrip(unittest.TestCase):
    def test_parsing_recovers_the_analysis(self):
        root, subs = tree()

        parsed, parsed_subs = parse_unified_analysis(store(root, subs))

        self.assertEqual(parsed.components, root.components)
        self.assertEqual(parsed.components_relations, root.components_relations)
        self.assertEqual(parsed_subs["1"].components, subs["1"][0].components)
        self.assertEqual(parsed_subs["1"].components_relations, subs["1"][0].components_relations)
        self.assertEqual(
            {path: entry.methods for path, entry in parsed.files.items()},
            {path: entry.methods for path, entry in root.files.items()},
        )

    def test_saving_a_parsed_analysis_writes_the_same_document(self):
        root, subs = tree()
        stored = store(root, subs)

        parsed, parsed_subs = parse_unified_analysis(stored)
        again = store(parsed, {cid: (sub, []) for cid, sub in parsed_subs.items()})

        del stored["metadata"]["generated_at"], again["metadata"]["generated_at"]
        self.assertEqual(again, stored)

    def test_an_endpoint_with_no_indexed_method_keeps_its_raw_key(self):
        root, subs = tree()
        root.components_relations[1].all_edges.append(edge("a.py|a.two", "c.py|vendor.absent", (8, 1)))

        doc = store(root, subs)
        parsed, _ = parse_unified_analysis(doc)

        self.assertIn("c.py|vendor.absent", [row[1] for row in doc["edges"]])
        self.assertEqual(parsed.components_relations[1].all_edges[1].target, ref("c.py", "vendor.absent"))

    def test_a_file_path_containing_a_pipe_is_written_and_read(self):
        root = AnalysisInsights(description="d", components=[], components_relations=[])
        root.files = {
            "we|ird.py": FileEntry(
                methods=[MethodEntry(qualified_name="fn", start_line=1, end_line=2, node_type="FUNCTION")]
            )
        }

        parsed, _ = parse_unified_analysis(store(root, {}))

        self.assertEqual([m.qualified_name for m in parsed.files["we|ird.py"].methods], ["fn"])


class TestWhatReadersCannotRebuildIsWrittenOut(unittest.TestCase):
    def test_a_relation_whose_edges_are_not_the_lift_keeps_them(self):
        root, subs = tree()
        subs["1"][0].components_relations[0].all_edges = [edge("a.py|a.one", "b.py|b.one", (99, 1))]

        doc = store(root, subs)
        _, parsed_subs = parse_unified_analysis(doc)

        self.assertIn("all_edges", doc["components"][0]["components_relations"][0])
        self.assertEqual(
            parsed_subs["1"].components_relations,
            merge_relations_by_pair(subs["1"][0].components_relations, include_relation=True),
        )

    def test_a_key_edge_whose_call_sites_differ_from_its_relations_keeps_them(self):
        """Two key edges on one pair with different call sites cannot both take the relation's."""
        root, subs = tree()
        root.components_relations[0].key_edges = [
            edge("a.py|a.one", "b.py|b.one", (5, 5), description="first"),
            edge("a.py|a.one", "b.py|b.one", (6, 6), description="second"),
        ]

        doc = store(root, subs)
        parsed, _ = parse_unified_analysis(doc)

        self.assertEqual(doc["components_relations"][0]["key_edges"][0][3], [[5, 5]])
        self.assertEqual(
            parsed.components_relations[0],
            merge_relations_by_pair([root.components_relations[0]], include_relation=True)[0],
        )

    def test_a_member_held_by_a_parent_but_no_child_is_kept(self):
        root, subs = tree()
        root.components[0].file_methods.append(FileMethodGroup(file_path="c.py", methods=[method("c.py", "c.one")]))

        doc = store(root, subs)
        parsed, _ = parse_unified_analysis(doc)

        self.assertEqual(len(doc["components"][0]["file_methods"]), 4)
        self.assertEqual(parsed.components[0].file_methods, root.components[0].file_methods)


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

        self.assertEqual(alone["a.py|a.one"], among_others["a.py|a.one"])
        self.assertRegex(alone["a.py|a.one"], r"^[A-Za-z0-9_-]{8}$")

    def test_two_methods_sharing_an_id_fail_the_write(self):
        with patch.object(analysis_json, "_symbol_id", return_value="AAAAAAAA"):
            with self.assertRaises(SymbolIdCollisionError):
                store(*tree())


class TestRejectedDocuments(unittest.TestCase):
    def test_a_document_without_a_format_version_is_rejected(self):
        with self.assertRaisesRegex(AnalysisFormatError, "predates format versioning"):
            parse_unified_analysis({"metadata": {}, "methods_index": {}})

    def test_another_format_version_is_rejected(self):
        with self.assertRaisesRegex(AnalysisFormatError, "'v2'"):
            parse_unified_analysis({"metadata": {"format_version": "v2"}})

    def test_an_id_with_no_method_is_rejected(self):
        doc = store(*tree())
        doc["components"][1]["file_methods"][0] = "missing0"

        with self.assertRaises(ValueError):
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
