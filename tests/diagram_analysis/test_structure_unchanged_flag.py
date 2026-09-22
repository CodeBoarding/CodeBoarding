"""The incremental early exit is stamped ``structure_unchanged`` only when the saved tree equals the loaded one."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents.agent_responses import AnalysisInsights, Component
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.diagram_generator import (
    DiagramGenerator,
    _IncrementalPreparation,
    _MembershipBaseline,
    StructureSignature,
    _structure_signature,
)
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis
from static_analyzer.clustering import ClusterScopeResult


def _component(component_id: str, name: str, members: dict[str, list[str]]) -> Component:
    return Component(
        name=name,
        description="",
        key_entities=[],
        component_id=component_id,
        file_methods=[
            FileMethodGroup(
                file_path=path,
                methods=[
                    MethodEntry(qualified_name=method, start_line=1, end_line=2, node_type="FUNCTION")
                    for method in methods
                ],
            )
            for path, methods in members.items()
        ],
    )


def _tree() -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """Two root components, the first expanded into two children that own exactly its methods."""
    root = AnalysisInsights(
        description="root",
        components=[
            _component("1", "Core", {"a.py": ["f", "h"]}),
            _component("2", "Util", {"b.py": ["g"]}),
        ],
        components_relations=[],
    )
    subs = {
        "1": AnalysisInsights(
            description="core",
            components=[
                _component("1.1", "Core F", {"a.py": ["f"]}),
                _component("1.2", "Core H", {"a.py": ["h"]}),
            ],
            components_relations=[],
        )
    }
    return root, subs


class TestStructureUnchangedFlag(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save(self, structure_unchanged: bool = False) -> Path:
        return save_analysis(
            analysis=AnalysisInsights(description="root", components=[], components_relations=[]),
            output_dir=self.temp_dir,
            sub_analyses={},
            repo_name="repo",
            repo_dir=self.temp_dir,
            source_tree_hash="abc",
            depth_cap=2,
            structure_unchanged=structure_unchanged,
        )

    def _metadata(self) -> dict:
        metadata = load_analysis_metadata(self.temp_dir)
        assert metadata is not None
        return metadata

    def _early_exit_flag(
        self, root: AnalysisInsights, subs: dict[str, AnalysisInsights], baseline: StructureSignature
    ) -> bool:
        """Run the early exit over a tree loaded as ``baseline`` and return the flag it saved."""
        gen = DiagramGenerator(
            repo_location=self.temp_dir,
            temp_folder=self.temp_dir,
            repo_name="repo",
            output_dir=self.temp_dir,
            depth_cap=2,
            run_id="run",
            log_path="log",
        )
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.available_cfgs.return_value = {}
        gen.clustering_hierarchy = ClusterScopeResult(scope_id=ROOT_SCOPE_ID)
        gen._incremental_preparation = _IncrementalPreparation(
            structure_changed=False,
            baseline_membership=_MembershipBaseline(),
            baseline_structure=baseline,
        )
        gen._refresh_files_index = Mock()
        gen._persist_static_analysis_artifact = Mock()
        with (
            patch(
                "diagram_analysis.diagram_generator.save_analysis", return_value=self.temp_dir / "analysis.json"
            ) as save,
            patch("diagram_analysis.diagram_generator.write_fingerprint"),
        ):
            gen.generate_analysis_incremental(root, subs)
        return save.call_args.kwargs["structure_unchanged"]

    def test_a_plain_save_is_not_marked(self) -> None:
        self._save()
        self.assertIs(self._metadata()["structure_unchanged"], False)

    def test_the_mark_describes_one_write_and_is_never_carried_forward(self) -> None:
        """Why: a later run that did re-detail must not inherit a verdict it did not earn."""
        self._save(structure_unchanged=True)
        self._save()
        self.assertIs(self._metadata()["structure_unchanged"], False)

    def test_an_early_exit_that_saves_the_loaded_tree_is_marked(self) -> None:
        root, subs = _tree()
        self.assertTrue(self._early_exit_flag(root, subs, _structure_signature(root, subs)))

    def test_a_deleted_file_scrubbed_from_a_component_is_not_unchanged(self) -> None:
        """Why: the scrub runs before the membership comparison that decides the early exit."""
        root, subs = _tree()
        baseline = _structure_signature(root, subs)
        root.components[1].file_methods = []
        self.assertFalse(self._early_exit_flag(root, subs, baseline))

    def test_a_child_scope_repaired_on_the_way_to_disk_is_not_unchanged(self) -> None:
        root, subs = _tree()
        subs["1"].components[1].file_methods = []
        baseline = _structure_signature(root, subs)
        self.assertFalse(self._early_exit_flag(root, subs, baseline))

    def test_a_legacy_single_child_scope_absorbed_by_finalization_is_not_unchanged(self) -> None:
        root, subs = _tree()
        subs["1"].components.pop()
        root.components[0].file_methods = [
            FileMethodGroup(file_path="a.py", methods=subs["1"].components[0].file_methods[0].methods)
        ]
        baseline = _structure_signature(root, subs)
        self.assertFalse(self._early_exit_flag(root, subs, baseline))


if __name__ == "__main__":
    unittest.main()
