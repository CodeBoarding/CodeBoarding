"""Test that concurrent processes writing to analysis.json don't lose each other's changes.

Uses multiprocessing (not threads) to match the real scenario where each
`--partial-component` invocation is a separate CLI process with its own
in-memory cache.
"""

import json
import multiprocessing
from pathlib import Path

import pytest

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from diagram_analysis.incremental.io_utils import save_analysis, save_sub_analysis, load_sub_analysis


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
                assigned_files=[f"src/{component_name.lower()}_sub1.py"],
                source_cluster_ids=[],
            ),
        ],
        components_relations=[],
    )


def _worker_save_sub_analysis(output_dir: str, component_name: str, barrier_parties: int) -> None:
    """Worker function that runs in a separate process.

    Uses a Barrier so all workers attempt save_sub_analysis at roughly the same time,
    maximizing the chance of lock contention.
    """
    # Recreate the sub-analysis in this process (can't pickle AnalysisInsights reliably across processes)
    sub_analysis = _make_sub_analysis(component_name)
    # Each process gets its own fresh cache — no stale data
    save_sub_analysis(sub_analysis, Path(output_dir), component_name)


@pytest.fixture
def root_analysis() -> AnalysisInsights:
    """Root analysis with 3 expandable components but no sub-analyses yet."""
    return AnalysisInsights(
        description="Test project",
        components=[
            Component(
                name="ComponentB",
                description="Component B",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_b.ClassB",
                        reference_file="src/module_b.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                assigned_files=["src/module_b.py"],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentC",
                description="Component C",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_c.ClassC",
                        reference_file="src/module_c.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                assigned_files=["src/module_c.py"],
                source_cluster_ids=[2],
            ),
            Component(
                name="ComponentD",
                description="Component D",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_d.ClassD",
                        reference_file="src/module_d.py",
                        reference_start_line=1,
                        reference_end_line=20,
                    )
                ],
                assigned_files=["src/module_d.py"],
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
            expandable_components=["ComponentB", "ComponentC", "ComponentD"],
            repo_name="test-repo",
            depth_level=2,
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
                args=(str(output_dir), name, len(component_names)),
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
            sub = load_sub_analysis(output_dir, name)
            assert sub is not None, f"load_sub_analysis returned None for {name}"
            # Verify sub-analysis has the expected sub-component
            sub_comp_names = [c.name for c in sub.components]
            assert f"{name}_Sub1" in sub_comp_names, f"Expected {name}_Sub1 in sub-analysis for {name}"

        # Verify root-level data is preserved
        assert final_data["metadata"]["repo_name"] == "test-repo"
        assert final_data["metadata"]["depth_level"] == 2
        assert len(final_data["components"]) == 3
        assert len(final_data["components_relations"]) == 1
