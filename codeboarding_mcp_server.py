"""MCP server exposing CodeBoarding's static analysis tools to OpenCode."""

import json
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _get_repo_context():
    """Create a RepoContext from environment variables."""
    from agents.tools.base import RepoContext
    from repo_utils.ignore import RepoIgnoreManager
    from static_analyzer import StaticAnalyzer

    repo_dir = Path(os.environ.get("CODEBOARDING_REPO_DIR", "."))
    analyzer = StaticAnalyzer(repo_dir)
    analyzer.__enter__()
    try:
        static_analysis = analyzer.analyze(cache_dir=repo_dir / ".codeboarding" / "artifacts")
        ignore_manager = RepoIgnoreManager(repo_dir)
    except Exception:
        analyzer.__exit__(None, None, None)
        raise
    return RepoContext(repo_dir=repo_dir, ignore_manager=ignore_manager, static_analysis=static_analysis), analyzer


def _cleanup(analyzer):
    """Clean up the static analyzer."""
    if analyzer:
        analyzer.__exit__(None, None, None)


_repo_context = None
_analyzer = None


def _get_context():
    global _repo_context, _analyzer
    if _repo_context is None:
        _repo_context, _analyzer = _get_repo_context()
    return _repo_context


def _run_tool(tool_name: str, **kwargs) -> str:
    """Run a CodeBoarding tool by name."""
    from agents.tools.toolkit import CodeBoardingToolkit

    context = _get_context()
    toolkit = CodeBoardingToolkit(context)

    tool_map = {
        "getSourceCode": toolkit.read_source_reference,
        "readFile": toolkit.read_file,
        "getFileStructure": toolkit.read_file_structure,
        "getClassHierarchy": toolkit.read_structure,
        "getPackageDependencies": toolkit.read_packages,
        "getControlFlowGraph": toolkit.read_cfg,
        "getMethodInvocations": toolkit.read_method_invocations,
        "readDocs": toolkit.read_docs,
        "readExternalDeps": toolkit.external_deps,
    }

    if tool_name not in tool_map:
        return f"Error: Unknown tool '{tool_name}'"

    try:
        return tool_map[tool_name]._run(**kwargs)
    except Exception as e:
        return f"Error: {tool_name} failed - {e}"


mcp = FastMCP("CodeBoarding")


@mcp.tool()
def getSourceCode(code_reference: str) -> str:
    """Retrieve source code by fully qualified import path."""
    return _run_tool("getSourceCode", code_reference=code_reference)


@mcp.tool()
def readFile(file_path: str, line_number: int) -> str:
    """Read file content centered on a target line number."""
    return _run_tool("readFile", file_path=file_path, line_number=line_number)


@mcp.tool()
def getFileStructure(dir: str = ".") -> str:
    """Return project directory structure as a tree."""
    return _run_tool("getFileStructure", dir=dir)


@mcp.tool()
def getClassHierarchy(class_qualified_name: str) -> str:
    """Retrieve class hierarchy and inheritance patterns."""
    return _run_tool("getClassHierarchy", class_qualified_name=class_qualified_name)


@mcp.tool()
def getPackageDependencies(root_package: str) -> str:
    """Show hierarchical relationships between modules and sub-packages."""
    return _run_tool("getPackageDependencies", root_package=root_package)


@mcp.tool()
def getControlFlowGraph() -> str:
    """Retrieve complete project control flow graph showing all method calls."""
    return _run_tool("getControlFlowGraph")


@mcp.tool()
def getMethodInvocations(method: str) -> str:
    """Retrieve method invocation relationships from CFG."""
    return _run_tool("getMethodInvocations", method=method)


@mcp.tool()
def readDocs(file_path: str = "README.md", line_number: int = 1) -> str:
    """Read project documentation files centered on a target line."""
    return _run_tool("readDocs", file_path=file_path, line_number=line_number)


@mcp.tool()
def readExternalDeps() -> str:
    """Scan repository for dependency manifest files."""
    return _run_tool("readExternalDeps")


if __name__ == "__main__":
    from logging_config import setup_logging

    setup_logging()
    mcp.run(transport="stdio")
