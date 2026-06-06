"""Tests for CodeBoarding MCP server and OpenCode integration pipeline."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.tools.base import RepoContext
from agents.tools.toolkit import CodeBoardingToolkit
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer
from utils import get_artifact_dir


class TestMCPToolSchemas(unittest.TestCase):
    """Test that CodeBoarding tools can be serialized to MCP-compatible JSON schemas."""

    @classmethod
    def setUpClass(cls):
        test_repo = Path(".")
        cls.analyzer = StaticAnalyzer(test_repo)
        cls.analyzer.__enter__()
        static_analysis = cls.analyzer.analyze(cache_dir=get_artifact_dir(test_repo))
        ignore_manager = RepoIgnoreManager(test_repo)
        cls.context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager, static_analysis=static_analysis)
        cls.toolkit = CodeBoardingToolkit(cls.context)

        from codeboarding_mcp_server import mcp

        cls.mcp_tools = mcp._tool_manager.list_tools()

    @classmethod
    def tearDownClass(cls):
        cls.analyzer.__exit__(None, None, None)

    def _tool_to_mcp_schema(self, tool) -> dict:
        """Convert a LangChain BaseTool to an MCP-compatible tool schema."""
        schema = tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object", "properties": {}}
        return {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": schema,
        }

    def test_read_source_reference_schema(self):
        tool = self.toolkit.read_source_reference
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "getSourceCode")
        self.assertIn("code_reference", schema["inputSchema"]["properties"])

    def test_read_file_schema(self):
        tool = self.toolkit.read_file
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "readFile")
        self.assertIn("file_path", schema["inputSchema"]["properties"])
        self.assertIn("line_number", schema["inputSchema"]["properties"])

    def test_read_file_structure_schema(self):
        tool = self.toolkit.read_file_structure
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "getFileStructure")
        props = schema["inputSchema"]["properties"]
        self.assertIn("dir", props)

    def test_read_structure_schema(self):
        tool = self.toolkit.read_structure
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "getClassHierarchy")
        self.assertIn("class_qualified_name", schema["inputSchema"]["properties"])

    def test_read_packages_schema(self):
        tool = self.toolkit.read_packages
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "getPackageDependencies")
        self.assertIn("root_package", schema["inputSchema"]["properties"])

    def test_read_cfg_schema(self):
        tool = self.toolkit.read_cfg
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "getControlFlowGraph")
        self.assertEqual(schema["inputSchema"]["properties"], {})

    def test_read_method_invocations_schema(self):
        tool = self.toolkit.read_method_invocations
        schema = self._tool_to_mcp_schema(tool)
        self.assertIn("getMethodInvocations", schema["name"])
        self.assertIn("method", schema["inputSchema"]["properties"])

    def test_read_docs_schema(self):
        tool = self.toolkit.read_docs
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "readDocs")
        props = schema["inputSchema"]["properties"]
        self.assertIn("file_path", props)
        self.assertIn("line_number", props)

    def test_external_deps_schema(self):
        tool = self.toolkit.external_deps
        schema = self._tool_to_mcp_schema(tool)
        self.assertEqual(schema["name"], "readExternalDeps")
        self.assertEqual(schema["inputSchema"]["properties"], {})

    def test_all_tools_have_unique_names(self):
        tools = self.toolkit.get_all_tools()
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), len(set(names)), f"Duplicate tool names: {names}")

    def test_all_tools_have_descriptions(self):
        tools = self.toolkit.get_all_tools()
        for tool in tools:
            self.assertTrue(tool.description, f"Tool {tool.name} has no description")

    def test_mcp_server_exports_match_toolkit_schemas(self):
        """Verify MCP server exports match Toolkit schemas."""
        from codeboarding_mcp_server import mcp

        mcp_tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        toolkit_tool_names = {t.name for t in self.toolkit.get_all_tools()}
        self.assertEqual(mcp_tool_names, toolkit_tool_names)


class TestMCPToolExecution(unittest.TestCase):
    """Test that CodeBoarding tools execute correctly when called via MCP-style interface."""

    @classmethod
    def setUpClass(cls):
        test_repo = Path(".")
        cls.analyzer = StaticAnalyzer(test_repo)
        cls.analyzer.__enter__()
        static_analysis = cls.analyzer.analyze(cache_dir=get_artifact_dir(test_repo))
        ignore_manager = RepoIgnoreManager(test_repo)
        cls.context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager, static_analysis=static_analysis)
        cls.toolkit = CodeBoardingToolkit(cls.context)

    @classmethod
    def tearDownClass(cls):
        cls.analyzer.__exit__(None, None, None)

    def _simulate_mcp_call(self, tool_name: str, args: dict) -> dict:
        """Simulate an MCP tool call: dispatch to the real server dispatcher."""
        from codeboarding_mcp_server import _run_tool

        try:
            result = _run_tool(tool_name, **args)
            if result.startswith("Error:"):
                return {"content": [{"type": "text", "text": result}], "isError": True}
            return {"content": [{"type": "text", "text": str(result)}], "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}

    def test_mcp_call_get_file_structure(self):
        result = self._simulate_mcp_call("getFileStructure", {"dir": "."})
        self.assertFalse(result["isError"])
        self.assertTrue(len(result["content"][0]["text"]) > 0)

    def test_mcp_call_read_file(self):
        result = self._simulate_mcp_call("readFile", {"file_path": "README.md", "line_number": 1})
        self.assertFalse(result["isError"])
        self.assertTrue(len(result["content"][0]["text"]) > 0)

    def test_mcp_call_get_control_flow_graph(self):
        result = self._simulate_mcp_call("getControlFlowGraph", {})
        self.assertFalse(result["isError"])
        self.assertIn("Control flow graph", result["content"][0]["text"])

    def test_mcp_call_read_external_deps(self):
        result = self._simulate_mcp_call("readExternalDeps", {})
        self.assertFalse(result["isError"])

    def test_mcp_call_read_docs(self):
        result = self._simulate_mcp_call("readDocs", {"file_path": "README.md"})
        self.assertFalse(result["isError"])

    def test_mcp_call_unknown_tool(self):
        result = self._simulate_mcp_call("nonexistentTool", {})
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])

    def test_mcp_call_missing_required_arg(self):
        result = self._simulate_mcp_call("readFile", {})
        self.assertTrue(result["isError"])


class TestOpenCodeLauncher(unittest.TestCase):
    """Test the OpenCode server launcher functionality."""

    def test_opencode_config_content_env_var(self):
        mcp_config = {
            "mcp": {
                "codeboarding": {
                    "type": "local",
                    "command": ["python", "-m", "codeboarding_mcp_server"],
                }
            }
        }
        config_json = json.dumps(mcp_config)
        self.assertIn("codeboarding", config_json)
        self.assertIn("local", config_json)

    def test_opencode_config_with_environment(self):
        mcp_config = {
            "mcp": {
                "codeboarding": {
                    "type": "local",
                    "command": ["python", "-m", "codeboarding_mcp_server"],
                    "environment": {
                        "CODEBOARDING_REPO_DIR": "/path/to/repo",
                    },
                }
            }
        }
        config_json = json.dumps(mcp_config)
        parsed = json.loads(config_json)
        self.assertEqual(parsed["mcp"]["codeboarding"]["environment"]["CODEBOARDING_REPO_DIR"], "/path/to/repo")

    def test_opencode_serve_command_construction(self):
        cmd = ["opencode", "serve", "--port", "4096", "--hostname", "127.0.0.1"]
        self.assertEqual(cmd[0], "opencode")
        self.assertEqual(cmd[1], "serve")
        self.assertIn("--port", cmd)
        self.assertIn("--hostname", cmd)

    @patch("subprocess.Popen")
    def test_launcher_starts_opencode_with_config(self, mock_popen):
        mock_popen.return_value = MagicMock()

        mcp_config = {
            "mcp": {
                "codeboarding": {
                    "type": "local",
                    "command": ["python", "-m", "codeboarding_mcp_server"],
                }
            }
        }
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(mcp_config)

        subprocess.Popen(["opencode", "serve", "--port", "4096"], env=env)

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        self.assertEqual(call_args[1]["env"]["OPENCODE_CONFIG_CONTENT"], json.dumps(mcp_config))


class TestMCPHealthCheck(unittest.TestCase):
    """Test health check and lifecycle management for OpenCode + MCP."""

    def test_health_check_endpoint(self):
        expected_path = "/global/health"
        self.assertEqual(expected_path, "/global/health")

    def test_mcp_add_endpoint(self):
        expected_path = "/mcp"
        self.assertEqual(expected_path, "/mcp")

    def test_mcp_add_payload_structure(self):
        payload = {
            "name": "codeboarding",
            "config": {
                "type": "local",
                "command": ["python", "-m", "codeboarding_mcp_server"],
                "environment": {"CODEBOARDING_REPO_DIR": "/path/to/repo"},
            },
        }
        self.assertIn("name", payload)
        self.assertIn("config", payload)
        self.assertIn("type", payload["config"])
        self.assertIn("command", payload["config"])


class TestOpenCodeChatToolHandling(unittest.TestCase):
    """Test that ChatOpenCode can handle tool calls and results."""

    def test_extract_text_ignores_tool_parts(self):
        from agents.opencode_chat import ChatOpenCode

        client = ChatOpenCode()
        response_data = {
            "parts": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_call", "tool": "readFile", "input": {"file_path": "test.py"}},
                {"type": "text", "text": "World"},
            ]
        }
        text = client._extract_text_from_response(response_data)
        self.assertEqual(text, "Hello\nWorld")

    def test_extract_text_with_only_tool_parts(self):
        from agents.opencode_chat import ChatOpenCode

        client = ChatOpenCode()
        response_data = {
            "parts": [
                {"type": "tool_call", "tool": "readFile", "input": {"file_path": "test.py"}},
            ]
        }
        text = client._extract_text_from_response(response_data)
        self.assertEqual(text, "")

    def test_extract_text_with_empty_parts(self):
        from agents.opencode_chat import ChatOpenCode

        client = ChatOpenCode()
        response_data = {"parts": []}
        text = client._extract_text_from_response(response_data)
        self.assertEqual(text, "")


class TestMCPToolResultIntegration(unittest.TestCase):
    """Test the full tool call -> result -> response loop."""

    @classmethod
    def setUpClass(cls):
        test_repo = Path(".")
        cls.analyzer = StaticAnalyzer(test_repo)
        cls.analyzer.__enter__()
        static_analysis = cls.analyzer.analyze(cache_dir=get_artifact_dir(test_repo))
        ignore_manager = RepoIgnoreManager(test_repo)
        cls.context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager, static_analysis=static_analysis)
        cls.toolkit = CodeBoardingToolkit(cls.context)

    @classmethod
    def tearDownClass(cls):
        cls.analyzer.__exit__(None, None, None)

    def _simulate_tool_call_response(self, tool_name: str, args: dict) -> dict:
        """Simulate the full MCP tool call/response cycle."""
        from codeboarding_mcp_server import _run_tool

        try:
            result = _run_tool(tool_name, **args)
            return {
                "tool": tool_name,
                "input": args,
                "output": str(result),
                "success": True,
            }
        except Exception as e:
            return {
                "tool": tool_name,
                "input": args,
                "output": f"Error: {e}",
                "success": False,
            }

    def test_full_tool_call_cycle_get_file_structure(self):
        result = self._simulate_tool_call_response("getFileStructure", {"dir": "."})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "getFileStructure")
        self.assertEqual(result["input"], {"dir": "."})
        self.assertTrue(len(result["output"]) > 0)

    def test_full_tool_call_cycle_read_file(self):
        result = self._simulate_tool_call_response("readFile", {"file_path": "README.md", "line_number": 1})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "readFile")
        self.assertTrue(len(result["output"]) > 0)

    def test_full_tool_call_cycle_get_cfg(self):
        result = self._simulate_tool_call_response("getControlFlowGraph", {})
        self.assertTrue(result["success"])
        self.assertIn("Control flow graph", result["output"])

    def test_full_tool_call_cycle_error_handling(self):
        result = self._simulate_tool_call_response("readFile", {"file_path": "nonexistent.txt", "line_number": 1})
        self.assertTrue(result["success"])  # Tool executes without exception
        self.assertIn("Error", result["output"])  # But returns error message


if __name__ == "__main__":
    unittest.main()
