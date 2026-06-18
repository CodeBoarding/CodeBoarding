import json
from pathlib import Path

from codeboarding_web.diagram import load_cytoscape


def _write_analysis(output_dir: Path) -> None:
    """Write a minimal valid unified analysis.json with one root component."""
    # Components live at the top level — no "analysis" wrapper.
    # Shape matches build_unified_analysis_json / parse_unified_analysis expectations.
    data = {
        "metadata": {
            "generated_at": "2024-01-01T00:00:00+00:00",
            "commit_hash": "",
            "repo_name": "test",
            "depth_level": 1,
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
                "component_id": "1",
                "name": "Core",
                "description": "core",
                "key_entities": [],
                "source_cluster_ids": [],
                "file_methods": [],
                "can_expand": False,
            }
        ],
        "components_relations": [],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(data), encoding="utf-8")


def test_returns_none_when_absent(tmp_path):
    assert load_cytoscape(tmp_path, "demo") is None


def test_returns_elements_for_present_analysis(tmp_path):
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo")
    assert result is not None
    ids = {e["data"]["id"] for e in result["elements"]}
    assert "Core" in ids


def test_returns_none_on_corrupt_json(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "analysis.json").write_text("{not json", encoding="utf-8")
    assert load_cytoscape(tmp_path, "demo") is None
