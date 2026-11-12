"""Tests for persisted analysis lookup helpers."""

import shutil
from pathlib import Path

import pytest

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.exceptions import AnalysisFormatError, UnreadableAnalysisError
from diagram_analysis.analysis_json import FileCoverageSummary
from diagram_analysis.io_utils import (
    analysis_exists,
    load_analysis_metadata,
    load_expandable_component_ids,
    load_full_analysis,
    save_analysis,
)

NO_COVERAGE = FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0)

LEGACY_ANALYSIS = Path(__file__).parents[1] / "fixtures" / "legacy_analysis.json"


def test_true_when_parseable_analysis_present(tmp_path: Path) -> None:
    save_analysis(
        AnalysisInsights(description="d", components=[], components_relations=[]),
        tmp_path,
        repo_dir=tmp_path,
        source_tree_hash="",
        repo_name="repo",
        sub_analyses={},
        file_coverage_summary=NO_COVERAGE,
        depth_cap=1,
        tree_spec={},
    )
    assert analysis_exists(tmp_path) is True


def test_false_when_file_missing(tmp_path: Path) -> None:
    assert analysis_exists(tmp_path) is False


def test_invalid_json_raises_instead_of_reading_as_absent(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("not json")
    with pytest.raises(UnreadableAnalysisError, match="not valid JSON"):
        analysis_exists(tmp_path)


def test_json_that_is_not_an_object_raises_for_its_metadata_too(tmp_path: Path) -> None:
    (tmp_path / "analysis.json").write_text("[]")
    with pytest.raises(UnreadableAnalysisError, match="not a JSON object"):
        load_analysis_metadata(tmp_path)


def test_an_older_format_raises_instead_of_reading_as_absent(tmp_path: Path) -> None:
    shutil.copy(LEGACY_ANALYSIS, tmp_path / "analysis.json")

    with pytest.raises(AnalysisFormatError) as caught:
        load_full_analysis(tmp_path)

    assert caught.value.found is None


def test_an_older_format_still_gives_its_metadata(tmp_path: Path) -> None:
    shutil.copy(LEGACY_ANALYSIS, tmp_path / "analysis.json")

    metadata = load_analysis_metadata(tmp_path)

    assert metadata is not None
    assert metadata["depth_cap"] == 2


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
        file_coverage_summary=NO_COVERAGE,
        depth_cap=1,
        tree_spec={},
    )

    assert load_expandable_component_ids(tmp_path) == {"1", "2", "1.1"}
