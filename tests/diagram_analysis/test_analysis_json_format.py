"""Tests for the on-disk analysis.json shape and its expansion back to v1."""

import json
import unittest
from pathlib import Path

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationCallSite,
    RelationEdge,
    SourceCodeReference,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from diagram_analysis.analysis_json import (
    ANALYSIS_FORMAT_VERSION,
    _method_key,
    build_unified_analysis_json,
    expand_analysis_document,
    parse_unified_analysis,
)


def method(qname: str, start: int = 1) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=start, end_line=start + 5, node_type="FUNCTION")


def component(cid: str, files: dict[str, list[str]]) -> Component:
    return Component(
        name=f"c{cid}",
        description=f"component {cid}",
        key_entities=[],
        component_id=cid,
        file_methods=[
            FileMethodGroup(file_path=path, methods=[method(q, i * 10 + 1) for i, q in enumerate(qnames)])
            for path, qnames in files.items()
        ],
    )


def analysis(*components: Component, files: dict[str, list[str]] | None = None) -> AnalysisInsights:
    insights = AnalysisInsights(description="d", components=list(components), components_relations=[])
    insights.files = {
        path: FileEntry(methods=[method(q, i * 10 + 1) for i, q in enumerate(qnames)], content_hash=f"h{path}")
        for path, qnames in (files or {}).items()
    }
    return insights


def build(root: AnalysisInsights, subs: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None) -> dict:
    expandable = [c for c in root.components if subs and c.component_id in subs]
    return json.loads(
        build_unified_analysis_json(
            analysis=root,
            expandable_components=expandable,
            repo_name="repo",
            repo_dir=Path("/repo"),
            source_tree_hash="tree",
            depth_cap=3,
            sub_analyses=subs,
        )
    )


class TestLeanDocumentOmitsDerivableFields(unittest.TestCase):
    def test_methods_index_values_drop_the_two_halves_of_their_key(self):
        doc = build(analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"]}))

        entry = doc["methods_index"]["a.py|a.one"]
        self.assertNotIn("file_path", entry)
        self.assertNotIn("qualified_name", entry)
        self.assertEqual(entry["start_line"], 1)
        self.assertEqual(entry["type"], "FUNCTION")

    def test_files_drop_method_keys_but_keep_hashes(self):
        doc = build(analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"]}))

        self.assertNotIn("method_keys", doc["files"]["a.py"])
        self.assertEqual(doc["files"]["a.py"]["content_hash"], "ha.py")

    def test_parent_components_drop_file_methods_and_leaves_keep_them(self):
        root = analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"], "b.py": ["b.one"]})
        sub = analysis(component("1.1", {"b.py": ["b.one"]}))
        doc = build(root, {"1": (sub, [])})

        self.assertEqual(doc["components"][0]["file_methods"], [])
        self.assertEqual(
            doc["components"][0]["components"][0]["file_methods"], [doc["methods_index"]["b.py|b.one"]["id"]]
        )

    def test_metadata_declares_the_format_version(self):
        doc = build(analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"]}))

        self.assertEqual(doc["metadata"]["format_version"], ANALYSIS_FORMAT_VERSION)


class TestInternedIdentity(unittest.TestCase):
    def _doc_with_edge(self, source: str, target: str) -> dict:
        root = analysis(component("1", {"a.py": ["a.one", "a.two"]}), files={"a.py": ["a.one", "a.two"]})
        root.components_relations = [
            Relation(
                relation="calls",
                src_name="c1",
                dst_name="c1",
                src_id="1",
                dst_id="1",
                all_edges=[
                    RelationEdge(
                        source=SourceCodeReference(qualified_name=source, reference_file="a.py"),
                        target=SourceCodeReference(qualified_name=target, reference_file="a.py"),
                        call_sites=[RelationCallSite(line=12, column=8)],
                    )
                ],
            )
        ]
        return build(root)

    def test_edge_endpoints_become_ids_and_call_sites_become_pairs(self):
        doc = self._doc_with_edge("a.one", "a.two")

        edge = doc["components_relations"][0]["all_edges"][0]
        self.assertEqual(edge["source"], doc["methods_index"]["a.py|a.one"]["id"])
        self.assertEqual(edge["target"], doc["methods_index"]["a.py|a.two"]["id"])
        self.assertEqual(edge["call_sites"], [[12, 8]])

    def test_an_endpoint_with_no_indexed_method_keeps_its_raw_key(self):
        """External and unresolved references have no symbol to point at; dropping them would lose the edge."""
        doc = self._doc_with_edge("a.one", "vendor.absent")

        edge = doc["components_relations"][0]["all_edges"][0]
        self.assertEqual(edge["target"], "a.py|vendor.absent")
        self.assertEqual(
            expand_analysis_document(doc)["components_relations"][0]["all_edges"][0]["target"], "a.py|vendor.absent"
        )

    def test_ids_are_a_pure_function_of_the_method_key(self):
        first = build(analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"]}))
        # A second file ahead of it in path order must not renumber anything.
        second = build(
            analysis(
                component("1", {"a.py": ["a.one"], "0.py": ["z.one"]}), files={"0.py": ["z.one"], "a.py": ["a.one"]}
            )
        )

        self.assertEqual(first["methods_index"]["a.py|a.one"]["id"], second["methods_index"]["a.py|a.one"]["id"])

    def test_ids_are_distinct_for_overloads_sharing_a_name(self):
        qnames = ["ns.Tag.Tag()", "ns.Tag.Tag(Guid id)"]
        doc = build(analysis(component("1", {"a.cs": qnames}), files={"a.cs": qnames}))

        ids = {entry["id"] for entry in doc["methods_index"].values()}
        self.assertEqual(len(ids), 2)


class TestExpandRestoresEverything(unittest.TestCase):
    def test_expand_rebuilds_all_three_derived_fields(self):
        root = analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"], "b.py": ["b.one"]})
        sub = analysis(component("1.1", {"b.py": ["b.one"]}))
        expanded = expand_analysis_document(build(root, {"1": (sub, [])}))

        self.assertEqual(expanded["methods_index"]["a.py|a.one"]["file_path"], "a.py")
        self.assertEqual(expanded["methods_index"]["a.py|a.one"]["qualified_name"], "a.one")
        self.assertEqual(expanded["files"]["a.py"]["method_keys"], ["a.py|a.one"])
        self.assertEqual(expanded["components"][0]["file_methods"], [{"file_path": "b.py", "methods": ["b.one"]}])

    def test_expand_does_not_mutate_its_argument(self):
        doc = build(analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"]}))
        expand_analysis_document(doc)

        self.assertNotIn("file_path", doc["methods_index"]["a.py|a.one"])

    def test_qualified_names_containing_a_pipe_survive_the_split(self):
        qname = "ns.Read(string path, Mode mode = A|B)"
        doc = build(analysis(component("1", {"a.cs": [qname]}), files={"a.cs": [qname]}))
        expanded = expand_analysis_document(doc)

        self.assertEqual(expanded["methods_index"][f"a.cs|{qname}"]["qualified_name"], qname)
        self.assertEqual(expanded["methods_index"][f"a.cs|{qname}"]["file_path"], "a.cs")

    def test_malformed_key_without_a_separator_raises(self):
        with self.assertRaises(ValueError):
            expand_analysis_document({"methods_index": {"no-separator": {"start_line": 1}}})


class TestVersionOneDocumentsStillLoad(unittest.TestCase):
    def _legacy(self) -> dict:
        return {
            "metadata": {"generated_at": "t", "repo_name": "r", "depth_level": 1, "depth_cap": 1},
            "description": "d",
            "files": {"a.py": {"method_keys": ["a.py|a.one"], "content_hash": "h", "module_hash": ""}},
            "methods_index": {
                "a.py|a.one": {
                    "file_path": "a.py",
                    "qualified_name": "a.one",
                    "start_line": 1,
                    "end_line": 6,
                    "type": "FUNCTION",
                    "content_hash": "",
                }
            },
            "components": [
                {
                    "name": "c1",
                    "component_id": "1",
                    "description": "d",
                    "key_entities": [],
                    "file_methods": [{"file_path": "a.py", "methods": ["a.one"]}],
                    "can_expand": False,
                    "components": [],
                    "components_relations": [],
                }
            ],
            "components_relations": [],
        }

    def test_legacy_document_passes_through_untouched(self):
        legacy = self._legacy()

        self.assertEqual(expand_analysis_document(legacy), legacy)

    def test_an_empty_parent_in_a_legacy_document_is_not_rebuilt(self):
        """A v1 writer spelled every member out, so an empty list there is a real value, not an omission."""
        legacy = self._legacy()
        legacy["components"][0]["file_methods"] = []
        legacy["components"][0]["components"] = [
            {
                "name": "c1.1",
                "component_id": "1.1",
                "description": "d",
                "key_entities": [],
                "file_methods": [{"file_path": "a.py", "methods": ["a.one"]}],
                "can_expand": False,
                "components": [],
                "components_relations": [],
            }
        ]

        self.assertEqual(expand_analysis_document(legacy)["components"][0]["file_methods"], [])


class TestRoundTrip(unittest.TestCase):
    def test_parsing_a_lean_document_recovers_the_analysis(self):
        root = analysis(component("1", {"a.py": ["a.one"]}), files={"a.py": ["a.one"], "b.py": ["b.one"]})
        sub = analysis(component("1.1", {"b.py": ["b.one"]}))
        parsed, subs = parse_unified_analysis(build(root, {"1": (sub, [])}))

        self.assertEqual([g.file_path for g in parsed.components[0].file_methods], ["b.py"])
        self.assertEqual([m.qualified_name for m in parsed.components[0].file_methods[0].methods], ["b.one"])
        self.assertEqual([c.component_id for c in subs["1"].components], ["1.1"])
        self.assertEqual(sorted(parsed.files), ["a.py", "b.py"])
        self.assertEqual([m.qualified_name for m in parsed.files["a.py"].methods], ["a.one"])


class TestMethodKeySeparator(unittest.TestCase):
    def test_a_pipe_in_the_file_path_is_rejected(self):
        with self.assertRaises(ValueError):
            _method_key("we|ird.py", "mod.fn")


if __name__ == "__main__":
    unittest.main()
