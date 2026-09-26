"""The `resources` section of the document: present where the pass found something, absent where not."""

import json
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.analysis_json import build_unified_analysis_json
from static_analyzer.wiring_results import Resource, ResourceChild, ResourceKind

REPO = Path("/repo")

POSTGRES = Resource(
    key="resource:db:postgres",
    kind=ResourceKind.DB,
    name="postgres",
    display_name="PostgreSQL",
    declared_by=("src/AppHost/Program.cs",),
    home_unit="src/Ordering.API",
    children=(ResourceChild(key="resource:db:postgres/db:catalogdb", kind=ResourceKind.DB, name="catalogdb"),),
)


def document(**written: object) -> dict:
    analysis = AnalysisInsights(description="a repository", components=[], components_relations=[])
    return json.loads(build_unified_analysis_json(analysis, [], "repo", REPO, "hash", 1, **written))  # type: ignore[arg-type]


class TestResourcesSection(unittest.TestCase):
    def test_a_run_that_found_none_writes_the_document_it_always_wrote(self) -> None:
        """Absent rather than empty: with the pass off, this is the document from before the section
        existed, byte for byte. An empty list would change every document ever written."""
        self.assertNotIn("resources", document())
        self.assertNotIn("resources", document(resources=[]))

    def test_a_resource_is_written_in_the_shape_of_the_output_contract(self) -> None:
        (written,) = document(resources=[POSTGRES])["resources"]

        self.assertEqual(sorted(written), ["children", "declared_by", "display_name", "key", "kind", "name"])
        self.assertEqual((written["key"], written["kind"], written["name"]), ("resource:db:postgres", "db", "postgres"))
        self.assertEqual(written["declared_by"], ["src/AppHost/Program.cs"])
        self.assertEqual(written["display_name"], "PostgreSQL")
        # `home` is a component id, which the pass cannot know; the PR that places these nodes writes it.
        self.assertNotIn("home", written)

    def test_what_a_server_holds_is_written_under_it(self) -> None:
        (written,) = document(resources=[POSTGRES])["resources"]

        self.assertEqual(
            written["children"],
            [{"key": "resource:db:postgres/db:catalogdb", "kind": "db", "name": "catalogdb", "owner": ""}],
        )


if __name__ == "__main__":
    unittest.main()
