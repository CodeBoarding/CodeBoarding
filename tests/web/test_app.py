"""Tests for the FastAPI app factory and routes."""

from pathlib import Path

from fastapi.testclient import TestClient

from codeboarding_web.app import create_app


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
    from codeboarding_web.state import RunBusyError

    monkeypatch.setattr(app.state.runner, "start", lambda *a, **k: (_ for _ in ()).throw(RunBusyError()))
    r = c.post("/api/run", json={"scope": "full"})
    assert r.status_code == 409
