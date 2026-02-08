"""Tests for monitoring writers module."""

import json
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from monitoring.writers import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin


class MockAgent(MonitoringMixin):
    """Mock agent for testing."""

    def __init__(
        self,
        name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model_name: str = "gpt-4",
    ):
        self._name = name
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._model_name = model_name

    def get_monitoring_results(self) -> dict:
        return {
            "token_usage": {
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "total_tokens": self._input_tokens + self._output_tokens,
            },
            "model_name": self._model_name,
            "agent_name": self._name,
        }


@pytest.fixture
def temp_monitoring_dir(tmp_path: Path) -> Path:
    """Create a temporary monitoring directory."""
    return tmp_path / "monitoring"


@pytest.fixture
def sample_agents():
    """Create sample agents for testing."""
    return {
        "agent1": MockAgent("agent1", input_tokens=100, output_tokens=50, model_name="gpt-4"),
        "agent2": MockAgent("agent2", input_tokens=200, output_tokens=100, model_name="claude-3"),
    }


class TestStreamingStatsWriter:
    """Tests for StreamingStatsWriter class."""

    def test_initialization(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that writer initializes correctly."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        assert writer.monitoring_dir == temp_monitoring_dir
        assert writer.agents_dict == sample_agents
        assert writer.repo_name == "test-repo"
        assert writer.interval == 5.0  # default
        assert writer._thread is None

    def test_custom_interval(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that custom interval is set correctly."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
            interval=10.0,
        )

        assert writer.interval == 10.0

    def test_custom_start_time(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that custom start time is set correctly."""
        custom_start = time.time() - 100  # 100 seconds ago
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
            start_time=custom_start,
        )

        assert writer.start_time == custom_start

    def test_llm_usage_file_property(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that llm_usage_file property returns correct path."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        assert writer.llm_usage_file == temp_monitoring_dir / "llm_usage.json"

    def test_start_creates_directory(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that start() creates monitoring directory."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        assert not temp_monitoring_dir.exists()
        writer.start()
        assert temp_monitoring_dir.exists()
        writer.stop()

    def test_start_sets_start_time(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that start() sets start_time if not provided."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
            start_time=None,
        )

        before_start = time.time()
        writer.start()
        after_start = time.time()
        writer.stop()

        assert writer.start_time is not None
        assert before_start <= writer.start_time <= after_start

    def test_stop_saves_files(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that stop() saves LLM usage and metadata files."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
            output_dir=str(temp_monitoring_dir / "output"),
        )

        writer.start()
        writer.stop()

        # Check that files were created
        assert (temp_monitoring_dir / "llm_usage.json").exists()
        assert (temp_monitoring_dir / "run_metadata.json").exists()

    def test_stop_with_error(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that stop() records error in metadata."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        writer.start()
        writer.stop(error="Test error message")

        # Check metadata
        metadata_file = temp_monitoring_dir / "run_metadata.json"
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["success"] is False
        assert metadata["error"] == "Test error message"

    def test_context_manager(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that writer works as context manager."""
        with StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        ) as writer:
            assert writer._thread is not None

        # After exiting context, files should be saved
        assert (temp_monitoring_dir / "llm_usage.json").exists()

    def test_context_manager_with_exception(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that context manager handles exceptions."""
        try:
            with StreamingStatsWriter(
                monitoring_dir=temp_monitoring_dir,
                agents_dict=sample_agents,
                repo_name="test-repo",
            ) as writer:
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Metadata should show failure
        metadata_file = temp_monitoring_dir / "run_metadata.json"
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["success"] is False
        assert "Test exception" in metadata["error"]

    def test_save_llm_usage_content(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that LLM usage is saved with correct content."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        writer.start()
        writer.stop()

        # Check LLM usage file
        llm_file = temp_monitoring_dir / "llm_usage.json"
        with open(llm_file) as f:
            data = json.load(f)

        assert "agents" in data
        assert "agent1" in data["agents"]
        assert "agent2" in data["agents"]
        assert data["agents"]["agent1"]["token_usage"]["input_tokens"] == 100
        assert data["agents"]["agent2"]["token_usage"]["input_tokens"] == 200

    def test_save_run_metadata_content(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that run metadata is saved with correct content."""
        output_dir = temp_monitoring_dir / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "test.json").write_text("{}")
        (output_dir / "test.md").write_text("# Test")

        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
            output_dir=str(output_dir),
        )

        writer.start()
        writer.stop()

        # Check metadata file
        metadata_file = temp_monitoring_dir / "run_metadata.json"
        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["repo_name"] == "test-repo"
        assert metadata["success"] is True
        assert metadata["error"] is None
        assert "timestamp" in metadata
        assert "duration_seconds" in metadata
        assert metadata["files_generated"]["json"] == 1
        assert metadata["files_generated"]["markdown"] == 1
        assert metadata["output_dir"] == str(output_dir)

    def test_no_agents_does_not_write(self, temp_monitoring_dir: Path):
        """Test that writer handles empty agents dict gracefully."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict={},
            repo_name="test-repo",
        )

        writer.start()
        writer.stop()

        # LLM file should not be created with empty agents
        llm_file = temp_monitoring_dir / "llm_usage.json"
        assert not llm_file.exists()

        # But metadata should still be created
        metadata_file = temp_monitoring_dir / "run_metadata.json"
        assert metadata_file.exists()

    def test_multiple_start_calls(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that multiple start() calls are handled gracefully."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        writer.start()
        first_thread = writer._thread
        writer.start()  # Second call should be a no-op

        assert writer._thread is first_thread
        writer.stop()

    def test_stop_without_start(self, temp_monitoring_dir: Path, sample_agents: dict):
        """Test that stop() without start() is handled gracefully."""
        writer = StreamingStatsWriter(
            monitoring_dir=temp_monitoring_dir,
            agents_dict=sample_agents,
            repo_name="test-repo",
        )

        # Should not raise
        writer.stop()
