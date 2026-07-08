"""Tests that DiagramGenerator stores and invokes progress_callback safely."""

from pathlib import Path

from diagram_analysis.diagram_generator import DiagramGenerator


def test_progress_callback_defaults_to_none(tmp_path: Path) -> None:
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
    )
    assert gen.progress_callback is None


def test_progress_callback_is_stored(tmp_path: Path) -> None:
    calls = []
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
        progress_callback=lambda: calls.append(1),
    )
    assert gen.progress_callback is not None
    gen.progress_callback()
    assert calls == [1]


def test_notify_progress_swallows_exceptions(tmp_path: Path) -> None:
    def boom() -> None:
        raise RuntimeError("nope")

    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
        progress_callback=boom,
    )
    gen._notify_progress()  # must not raise


def test_notify_progress_invokes_callback(tmp_path: Path) -> None:
    calls = []
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
        progress_callback=lambda: calls.append(1),
    )
    gen._notify_progress()
    assert calls == [1]
