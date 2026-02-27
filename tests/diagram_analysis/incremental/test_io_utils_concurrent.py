"""Test that concurrent processes writing to analysis.json don't lose each other's changes.

Uses multiprocessing (not threads) to match the real scenario where each
`--partial-component` invocation is a separate CLI process with its own
in-memory cache.
"""

import json
import multiprocessing
from pathlib import Path

import pytest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    Relation,
    SourceCodeReference,
    hash_component_id,
    ROOT_PARENT_ID,
)
from diagram_analysis.incremental.io_utils import save_analysis, save_sub_analysis, load_sub_analysis


COMP_B_ID = hash_component_id(ROOT_PARENT_ID, "ComponentB")
COMP_C_ID = hash_component_id(ROOT_PARENT_ID, "ComponentC")
COMP_D_ID = hash_component_id(ROOT_PARENT_ID, "ComponentD")
NAME_TO_ID = {"ComponentB": COMP_B_ID, "ComponentC": COMP_C_ID, "ComponentD": COMP_D_ID}


def _make_sub_analysis(component_name: str) -> AnalysisInsights:
    """Create a unique sub-analysis for a component."""
    return AnalysisInsights(
        description=f"Sub-analysis for {component_name}",
        components=[
            Component(
                name=f"{component_name}_Sub1",
                description=f"First sub-component of {component_name}",
                key_entities=[
                    SourceCodeReference(
                        qualified_name=f"{component_name.lower()}.sub1",
                        reference_file=f"src/{component_name.lower()}_sub1.py",
                        reference_start_line=1,
                        reference_end_line=10,
                    )
                ],
                file_methods=[FileMethodGroup(file_path=f"src/{component_name.lower()}_sub1.py")],
                source_cluster_ids=[],
            ),
        ],
        components_relations=[],
    )


def _worker_save_sub_analysis(output_dir: str, component_name: str, component_id: str) -> None:
    """Worker function that runs in a separate process.

    Multiple processes call save_sub_analysis concurrently, maximizing the chance of lock contention.
    """
    # Recreate the sub-analysis in this process (can't pickle AnalysisInsights reliably across processes)
    sub_analysis = _make_sub_analysis(component_name)
    # Each process gets its own fresh cache — no stale data
    save_sub_analysis(sub_analysis, Path(output_dir), component_id)


@pytest.fixture
def root_analysis() -> AnalysisInsights:
    """Root analysis with 3 expandable components but no sub-analyses yet."""
    return AnalysisInsights(
        description="Test project",
        components=[
            Component(
                name="ComponentB",
                component_id=COMP_B_ID,
                description="Component B",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_b.ClassB",
                        reference_file="src/module_b.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                file_methods=[FileMethodGroup(file_path="src/module_b.py")],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentC",
                component_id=COMP_C_ID,
                description="Component C",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_c.ClassC",
                        reference_file="src/module_c.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                file_methods=[FileMethodGroup(file_path="src/module_c.py")],
                source_cluster_ids=[2],
            ),
            Component(
                name="ComponentD",
                component_id=COMP_D_ID,
                description="Component D",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_d.ClassD",
                        reference_file="src/module_d.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                file_methods=[FileMethodGroup(file_path="src/module_d.py")],
                source_cluster_ids=[3],
            ),
        ],
        components_relations=[
            Relation(relation="calls", src_name="ComponentB", dst_name="ComponentC"),
        ],
    )


class TestConcurrentSaveSubAnalysis:
    """Verify that concurrent processes calling save_sub_analysis don't lose writes."""

    def test_parallel_saves_preserve_all_sub_analyses(self, tmp_path: Path, root_analysis: AnalysisInsights) -> None:
        """Three processes each save a different sub-analysis; all three must be present at the end."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Write initial analysis.json with root only (no sub-analyses)
        save_analysis(
            root_analysis,
            output_dir,
            expandable_component_ids=[COMP_B_ID, COMP_C_ID, COMP_D_ID],
            repo_name="test-repo",
        )

        # Verify initial state: file exists, no sub-analyses
        with open(output_dir / "analysis.json") as f:
            initial_data = json.load(f)
        for comp in initial_data["components"]:
            assert comp.get("components") is None, f"Component {comp['name']} should have no sub-analysis initially"

        # Spawn 3 processes that each save a different sub-analysis concurrently
        component_names = ["ComponentB", "ComponentC", "ComponentD"]
        processes = []
        for name in component_names:
            p = multiprocessing.Process(
                target=_worker_save_sub_analysis,
                args=(str(output_dir), name, NAME_TO_ID[name]),
            )
            processes.append(p)

        # Start all processes
        for p in processes:
            p.start()

        # Wait for all to finish
        for p in processes:
            p.join(timeout=30)
            assert p.exitcode == 0, f"Process for component exited with code {p.exitcode}"

        # Read the final analysis.json and verify ALL 3 sub-analyses are present
        with open(output_dir / "analysis.json") as f:
            final_data = json.load(f)

        components_with_subs = {comp["name"] for comp in final_data["components"] if comp.get("components") is not None}

        assert components_with_subs == {"ComponentB", "ComponentC", "ComponentD"}, (
            f"Expected all 3 sub-analyses to be present, but only found: {components_with_subs}. "
            "This indicates a concurrent write lost data."
        )

        # Also verify via the load_sub_analysis API
        for name in component_names:
            cid = NAME_TO_ID[name]
            sub = load_sub_analysis(output_dir, cid)
            assert sub is not None, f"load_sub_analysis returned None for {name}"
            # Verify sub-analysis has the expected sub-component
            sub_comp_names = [c.name for c in sub.components]
            assert f"{name}_Sub1" in sub_comp_names, f"Expected {name}_Sub1 in sub-analysis for {name}"

        # Verify root-level data is preserved
        assert final_data["metadata"]["repo_name"] == "test-repo"
        assert final_data["metadata"]["depth_level"] == 2
        assert len(final_data["components"]) == 3
        assert len(final_data["components_relations"]) == 1

    def test_save_analysis_preserves_nested_can_expand_eligibility(self, tmp_path: Path) -> None:
        """Nested can_expand should follow planner eligibility, not only existing sub-analysis keys."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        root_id = hash_component_id(ROOT_PARENT_ID, "RootComponent")
        child_expandable_id = hash_component_id(root_id, "ChildExpandable")
        child_leaf_id = hash_component_id(root_id, "ChildLeaf")

        root_analysis = AnalysisInsights(
            description="Root analysis",
            components=[
                Component(
                    name="RootComponent",
                    component_id=root_id,
                    description="Root",
                    key_entities=[],
                    file_methods=[FileMethodGroup(file_path="src/root.py")],
                    source_cluster_ids=[1],
                )
            ],
            components_relations=[],
        )

        root_sub_analysis = AnalysisInsights(
            description="Sub analysis",
            components=[
                Component(
                    name="ChildExpandable",
                    component_id=child_expandable_id,
                    description="Expandable child",
                    key_entities=[],
                    file_methods=[FileMethodGroup(file_path="src/child_expandable.py")],
                    source_cluster_ids=[10],
                ),
                Component(
                    name="ChildLeaf",
                    component_id=child_leaf_id,
                    description="Leaf child",
                    key_entities=[],
                    source_cluster_ids=[],
                ),
            ],
            components_relations=[],
        )

        save_analysis(
            analysis=root_analysis,
            output_dir=output_dir,
            expandable_component_ids=[root_id],
            sub_analyses={root_id: root_sub_analysis},
            repo_name="test-repo",
        )

        with open(output_dir / "analysis.json") as f:
            data = json.load(f)

        root = data["components"][0]
        assert root["can_expand"] is True
        assert root["components"] is not None

        child_expandable = next(c for c in root["components"] if c["component_id"] == child_expandable_id)
        child_leaf = next(c for c in root["components"] if c["component_id"] == child_leaf_id)

        # ChildExpandable is planner-eligible (has clusters) even without its own sub-analysis yet.
        assert child_expandable["can_expand"] is True
        assert child_expandable.get("components") is None
        assert child_leaf["can_expand"] is False
