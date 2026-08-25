"""Tests for persisted analysis lookup helpers."""

from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.io_utils import analysis_exists, load_expandable_component_ids, save_analysis


def test_true_when_parseable_analysis_present(tmp_path: Path) -> None:
    save_analysis(
        AnalysisInsights(description="d", components=[], components_relations=[]),
        tmp_path,
        repo_dir=tmp_path,
        source_tree_hash="",
        repo_name="repo",
    )
    assert analysis_exists(tmp_path) is True


def test_false_when_file_missing(tmp_path: Path) -> None:
    assert analysis_exists(tmp_path) is False


def test_false_when_file_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("not json")
    assert analysis_exists(tmp_path) is False


def test_load_expandable_component_ids_includes_nested_components(tmp_path: Path) -> None:
    root = AnalysisInsights(
        description="root",
        components=[
            Component(name="Target", component_id="1", description="", key_entities=[]),
            Component(name="Sibling", component_id="2", description="", key_entities=[]),
        ],
        components_relations=[],
    )
    sub_analyses = {
        "1": AnalysisInsights(
            description="target",
            components=[Component(name="Child", component_id="1.1", description="", key_entities=[])],
            components_relations=[],
        )
    }
    save_analysis(
        root,
        tmp_path,
        repo_dir=tmp_path,
        source_tree_hash="source-hash",
        expandable_component_ids=["1", "2"],
        sub_analyses=sub_analyses,
        sub_expandable_ids={"1": ["1.1"]},
        repo_name="repo",
    )

    assert load_expandable_component_ids(tmp_path) == {"1", "2", "1.1"}
