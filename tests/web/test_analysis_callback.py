"""Tests that workflow builder functions forward progress_callback to DiagramGenerator."""

import inspect
from pathlib import Path

import pytest

from codeboarding_workflows import analysis


def test_build_generator_forwards_callback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = {}

    class FakeGen:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(analysis, "DiagramGenerator", FakeGen)

    def cb() -> None:
        return None

    analysis.build_generator(
        repo_name="demo",
        repo_path=tmp_path,
        output_dir=tmp_path,
        run_id="r1",
        log_path=str(tmp_path),
        depth_level=1,
        progress_callback=cb,
    )
    assert captured["progress_callback"] is cb


def test_run_full_accepts_callback() -> None:
    assert "progress_callback" in inspect.signature(analysis.run_full).parameters


def test_run_incremental_accepts_callback() -> None:
    assert "progress_callback" in inspect.signature(analysis.run_incremental).parameters
