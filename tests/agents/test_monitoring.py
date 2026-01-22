import os
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from monitoring import MonitoringCallback
from monitoring.stats import RunStats
from monitoring.context import current_step
from langchain_core.outputs import LLMResult


class TestMonitoringCallback(unittest.TestCase):
    def setUp(self):
        self.stats = RunStats()
        self.callback = MonitoringCallback(stats_container=self.stats)

    def test_init(self):
        # Test initialization
        self.assertEqual(self.callback.stats.input_tokens, 0)
        self.assertEqual(self.callback.stats.output_tokens, 0)
        self.assertEqual(self.callback.stats.total_tokens, 0)
        self.assertEqual(len(self.callback.stats.tool_counts), 0)
        self.assertEqual(len(self.callback.stats.tool_errors), 0)

    def test_on_llm_end_with_usage(self):
        # Test on_llm_end with token usage
        llm_output = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        }
        response = LLMResult(generations=[], llm_output=llm_output)

        self.callback.on_llm_end(response)

        self.assertEqual(self.callback.stats.input_tokens, 100)
        self.assertEqual(self.callback.stats.output_tokens, 50)
        self.assertEqual(self.callback.stats.total_tokens, 150)

    def test_on_llm_end_without_total_tokens(self):
        # Test on_llm_end when total_tokens is not provided
        llm_output = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            }
        }
        response = LLMResult(generations=[], llm_output=llm_output)

        self.callback.on_llm_end(response)

        self.assertEqual(self.callback.stats.input_tokens, 100)
        self.assertEqual(self.callback.stats.output_tokens, 50)
        # Should calculate total from prompt + completion
        self.assertEqual(self.callback.stats.total_tokens, 150)

    def test_on_llm_end_accumulates(self):
        # Test that multiple calls accumulate
        llm_output1 = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        }
        response1 = LLMResult(generations=[], llm_output=llm_output1)

        llm_output2 = {
            "token_usage": {
                "prompt_tokens": 80,
                "completion_tokens": 40,
                "total_tokens": 120,
            }
        }
        response2 = LLMResult(generations=[], llm_output=llm_output2)

        self.callback.on_llm_end(response1)
        self.callback.on_llm_end(response2)

        self.assertEqual(self.callback.stats.input_tokens, 180)
        self.assertEqual(self.callback.stats.output_tokens, 90)
        self.assertEqual(self.callback.stats.total_tokens, 270)

    def test_on_llm_end_no_usage(self):
        # Test on_llm_end with no token usage
        response = LLMResult(generations=[], llm_output={})

        self.callback.on_llm_end(response)

        # Should not crash, counts remain 0
        self.assertEqual(self.callback.stats.input_tokens, 0)
        self.assertEqual(self.callback.stats.output_tokens, 0)

    def test_on_llm_end_logs_expected_format(self):
        # As we use the logs as communication at the moment, I am adding a test to validate that the structure doesn't change
        # Ensure the log format remains stable for downstream parsing
        llm_output = {
            "token_usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
            }
        }
        response = LLMResult(generations=[], llm_output=llm_output)

        token = current_step.set("dummyStep")
        try:
            self.callback.model_name = "dummyModel"
            with patch("monitoring.callbacks.logger.info") as mock_info:
                self.callback.on_llm_end(response)

                expected = (
                    "Token Usage: step=dummyStep model=dummyModel "
                    'usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}'
                )
                mock_info.assert_called_once_with(expected)
        finally:
            current_step.reset(token)

    def test_on_tool_start(self):
        # Test on_tool_start
        serialized = {"name": "test_tool"}
        run_id = str(uuid4())

        self.callback.on_tool_start(serialized, "input", run_id=run_id)

        self.assertEqual(self.callback.stats.tool_counts["test_tool"], 1)
        self.assertIn(run_id, self.callback._tool_start_times)
        self.assertIn(run_id, self.callback._tool_names)

    def test_on_tool_start_multiple_calls(self):
        # Test multiple tool starts
        serialized1 = {"name": "tool1"}
        serialized2 = {"name": "tool2"}
        serialized3 = {"name": "tool1"}

        self.callback.on_tool_start(serialized1, "input", run_id=str(uuid4()))
        self.callback.on_tool_start(serialized2, "input", run_id=str(uuid4()))
        self.callback.on_tool_start(serialized3, "input", run_id=str(uuid4()))

        self.assertEqual(self.callback.stats.tool_counts["tool1"], 2)
        self.assertEqual(self.callback.stats.tool_counts["tool2"], 1)

    def test_on_tool_end(self):
        # Test on_tool_end
        serialized = {"name": "test_tool"}
        run_id = str(uuid4())

        # Start tool
        self.callback.on_tool_start(serialized, "input", run_id=run_id)

        # Sleep briefly to measure latency
        time.sleep(0.01)

        # End tool
        self.callback.on_tool_end("output", run_id=run_id)

        # Check latency was recorded
        self.assertIn("test_tool", self.callback.stats.tool_latency_ms)
        self.assertEqual(len(self.callback.stats.tool_latency_ms["test_tool"]), 1)
        self.assertGreater(self.callback.stats.tool_latency_ms["test_tool"][0], 0)

        # Check cleanup
        self.assertNotIn(run_id, self.callback._tool_start_times)
        self.assertNotIn(run_id, self.callback._tool_names)

    def test_on_tool_end_without_start(self):
        # Test on_tool_end without corresponding start
        run_id = str(uuid4())

        # Should not crash
        self.callback.on_tool_end("output", run_id=run_id)

    def test_on_tool_error(self):
        # Test on_tool_error
        serialized = {"name": "test_tool"}
        run_id = uuid4()

        # Start tool
        self.callback.on_tool_start(serialized, "input", run_id=str(run_id))

        # Trigger error - on_tool_error expects run_id as keyword argument
        error = ValueError("Test error")
        self.callback.on_tool_error(error, run_id=run_id, parent_run_id=None)

        # Check error was recorded
        self.assertGreaterEqual(self.callback.stats.tool_errors["test_tool"], 1)

        # Check cleanup - the callback uses str(run_id) in _tool_names/_tool_start_times
        self.assertNotIn(str(run_id), self.callback._tool_start_times)
        self.assertNotIn(str(run_id), self.callback._tool_names)

    def test_on_tool_error_without_start(self):
        # Test on_tool_error without corresponding start
        run_id = uuid4()
        error = ValueError("Test error")

        # Should not crash
        self.callback.on_tool_error(error, run_id=run_id, parent_run_id=None)

        self.assertEqual(self.callback.stats.tool_errors["unknown_tool"], 1)

    def test_tool_name_from_id(self):
        # Test extracting tool name from id field
        serialized = {"id": "custom_tool"}  # id should be a string, not list
        run_id = str(uuid4())

        self.callback.on_tool_start(serialized, "input", run_id=run_id)

        # Should use id if name is not present
        self.assertIn("custom_tool", self.callback.stats.tool_counts)

    def test_tool_name_from_lc_namespace(self):
        # Test extracting tool name from lc_namespace
        serialized = {"lc_namespace": ["langchain", "tools", "my_tool"]}
        run_id = str(uuid4())

        self.callback.on_tool_start(serialized, "input", run_id=run_id)

        # Should use last element of lc_namespace
        self.assertIn("my_tool", self.callback.stats.tool_counts)


if __name__ == "__main__":
    unittest.main()
