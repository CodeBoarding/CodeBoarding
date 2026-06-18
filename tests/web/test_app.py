"""Tests for the FastAPI app factory and routes."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from codeboarding_web.app import create_app
from codeboarding_web.state import RunBusyError

# component_id of the expandable component used across tests
_EXPANDABLE_ID = "1"


def _write_analysis(output_dir: Path) -> None:
    """Write a minimal valid unified analysis.json with enrichment-ready fixture data.

    Component "Core" (component_id="1") has a key_entity and a nested sub-component
    so that parse_unified_analysis yields a non-empty sub_analyses dict, making
    "Core" expandable and its keyEntities list populated.
    """
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


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo"))


def test_status_idle_no_baseline(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "idle"
    assert body["has_baseline"] is False


def test_diagram_json_404_when_absent(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/diagram.json").status_code == 404


def test_run_rejects_bad_scope(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/run", json={"scope": "nope"})
    assert r.status_code == 422 or r.status_code == 400


def test_run_conflict_when_busy(tmp_path: Path, monkeypatch) -> None:
    app = create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo")
    c = TestClient(app)

    monkeypatch.setattr(app.state.runner, "start", lambda *a, **k: (_ for _ in ()).throw(RunBusyError()))
    r = c.post("/api/run", json={"scope": "full"})
    assert r.status_code == 409


def test_status_includes_watch_enabled(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/status").json()["watch_enabled"] is False


def test_watch_toggle(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/watch", json={"enabled": True})
    assert r.status_code == 200 and r.json()["watch_enabled"] is True
    assert c.get("/api/status").json()["watch_enabled"] is True


def test_diagram_component_404_when_absent(tmp_path: Path) -> None:
    """GET /api/diagram/<id> → 404 when no analysis.json exists."""
    c = _client(tmp_path)
    assert c.get("/api/diagram/some_component").status_code == 404


def test_diagram_component_200_for_valid_id(tmp_path: Path) -> None:
    """GET /api/diagram/<id> → 200 with elements key when component exists."""
    _write_analysis(tmp_path)
    c = TestClient(create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo"))
    r = c.get(f"/api/diagram/{_EXPANDABLE_ID}")
    assert r.status_code == 200
    assert "elements" in r.json()


def test_component_diff_404_when_no_analysis(tmp_path: Path) -> None:
    """GET /api/component/<id>/diff → 404 when analysis.json is absent."""
    c = _client(tmp_path)
    assert c.get(f"/api/component/{_EXPANDABLE_ID}/diff").status_code == 404


def test_component_diff_404_unknown_id(tmp_path: Path) -> None:
    """GET /api/component/<id>/diff → 404 when component_id is unknown."""
    _write_analysis(tmp_path)
    c = TestClient(create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo"))
    assert c.get("/api/component/nonexistent-id/diff").status_code == 404


def test_component_diff_200_has_files_and_diff_keys(tmp_path: Path) -> None:
    """GET /api/component/<id>/diff → 200 with files and diff keys for a known id."""
    _write_analysis(tmp_path)
    c = TestClient(create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo"))
    r = c.get(f"/api/component/{_EXPANDABLE_ID}/diff")
    assert r.status_code == 200
    body = r.json()
    assert "files" in body
    assert "diff" in body
    assert body["component_id"] == _EXPANDABLE_ID
    # The fixture component has key_entity with reference_file "core/main.py"
    assert body["files"] == ["core/main.py"]
    # tmp_path is not a real git repo so diff is empty string
    assert isinstance(body["diff"], str)
