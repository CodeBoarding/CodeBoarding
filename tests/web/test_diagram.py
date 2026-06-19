"""Tests for codeboarding_web.diagram cytoscape loaders."""

import json
from pathlib import Path

from codeboarding_web.diagram import load_cytoscape, load_cytoscape_component

# component_id of the expandable component used across tests
_EXPANDABLE_ID = "1"


def _write_analysis(output_dir: Path) -> None:
    """Write a minimal valid unified analysis.json with one expandable, enriched component."""
    data = {
        "metadata": {
            "generated_at": "2024-01-01T00:00:00+00:00",
            "commit_hash": "",
            "repo_name": "test",
            "depth_level": 2,
            "file_coverage_summary": {
                "total_files": 0,
                "analyzed": 0,
                "not_analyzed": 0,
                "not_analyzed_by_reason": {},
            },
        },
        "description": "test",
        "files": {},
        "methods_index": {},
        "components": [
            {
                "component_id": _EXPANDABLE_ID,
                "name": "Core",
                "description": "core",
                "key_entities": [
                    {
                        "qualified_name": "core.main",
                        "reference_file": "core/main.py",
                        "reference_start_line": 10,
                        "reference_end_line": 20,
                    }
                ],
                "source_cluster_ids": [],
                "file_methods": [],
                "can_expand": True,
                # nested sub-components make parse_unified_analysis register this id
                "components": [
                    {
                        "component_id": "1.1",
                        "name": "SubCore",
                        "description": "sub",
                        "key_entities": [],
                        "source_cluster_ids": [],
                        "file_methods": [],
                        "can_expand": False,
                    }
                ],
                "components_relations": [],
            }
        ],
        "components_relations": [],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(data), encoding="utf-8")


def test_returns_none_when_absent(tmp_path: Path) -> None:
    assert load_cytoscape(tmp_path, "demo", tmp_path) is None


def test_returns_elements_for_present_analysis(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    ids = {e["data"]["id"] for e in result["elements"]}
    assert "Core" in ids


def test_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "analysis.json").write_text("{not json", encoding="utf-8")
    assert load_cytoscape(tmp_path, "demo", tmp_path) is None


def test_overview_nodes_enriched(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    assert "componentId" in node["data"]
    assert "expandable" in node["data"]
    assert "keyEntities" in node["data"]


def test_overview_node_expandable_flag(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    assert node["data"]["expandable"] is True


def test_overview_node_key_entities_populated(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    entities = node["data"]["keyEntities"]
    assert len(entities) == 1
    assert entities[0]["qname"] == "core.main"
    assert entities[0]["startLine"] == 10
    # openUrl uses tmp_path as repo_path so it should be set
    assert entities[0]["openUrl"] is not None
    assert entities[0]["openUrl"].startswith("vscode://file/")


def test_component_subgraph_loads(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    sub = load_cytoscape_component(tmp_path, "demo", tmp_path, _EXPANDABLE_ID)
    assert sub is not None
    assert "elements" in sub


def test_component_subgraph_missing_returns_none(tmp_path: Path) -> None:
    _write_analysis(tmp_path)
    assert load_cytoscape_component(tmp_path, "demo", tmp_path, "nonexistent") is None


def test_component_subgraph_absent_file_returns_none(tmp_path: Path) -> None:
    assert load_cytoscape_component(tmp_path, "demo", tmp_path, _EXPANDABLE_ID) is None


def test_overview_node_has_source_files_warnings_modifications(tmp_path: Path) -> None:
    """Overview nodes must carry sourceFiles, warnings, modifications, and fileWarnings keys."""
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    data = node["data"]
    assert "sourceFiles" in data
    assert "warnings" in data
    assert "modifications" in data
    # The fixture has one key_entity with file "core/main.py" → sourceFiles non-empty
    assert data["sourceFiles"] == ["core/main.py"]
    # No health_report.json or git changes in tmp_path → defaults to 0
    assert data["warnings"] == 0
    assert data["modifications"] == 0


def test_overview_node_has_file_warnings_key(tmp_path: Path) -> None:
    """fileWarnings must be a list; empty when no health_report.json exists."""
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    fw = node["data"]["fileWarnings"]
    assert isinstance(fw, list)
    # No health_report.json → no entries
    assert fw == []


def test_overview_node_file_warnings_with_health_report(tmp_path: Path) -> None:
    """fileWarnings entries have {file, warnings} shape, sorted by count desc then path."""
    _write_analysis(tmp_path)
    # Write a health report that matches the fixture's component file "core/main.py"
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "health_report.json").write_text(
        json.dumps(
            {
                "file_summaries": [
                    {"file_path": "core/main.py", "warning_findings": 3},
                    {"file_path": "other/unrelated.py", "warning_findings": 5},
                ]
            }
        ),
        encoding="utf-8",
    )
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    assert result is not None
    node = next(e for e in result["elements"] if "source" not in e["data"])
    fw = node["data"]["fileWarnings"]
    assert isinstance(fw, list)
    # Only "core/main.py" belongs to the component; "other/unrelated.py" does not
    assert len(fw) == 1
    assert fw[0]["file"] == "core/main.py"
    assert fw[0]["warnings"] == 3
