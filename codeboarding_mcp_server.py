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
    static_analysis = analyzer.analyze(cache_dir=repo_dir / ".codeboarding" / "artifacts")
    ignore_manager = RepoIgnoreManager(repo_dir)
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
        return f"Error running {tool_name}: {e}"


mcp = FastMCP("CodeBoarding")


@mcp.tool()
def getSourceCode(code_reference: str) -> str:
    """Retrieves source code for specific classes, methods, or functions by fully qualified import path.
    Use only when CFG analysis lacks critical implementation details.
    Examples: 'django.core.management.base.BaseCommand', 'langchain.tools.tool'.
    Do not include file extensions (.py) or relative paths.
    Note: Each call is expensive - prefer analyzing CFG data first."""
    return _run_tool("getSourceCode", code_reference=code_reference)


@mcp.tool()
def readFile(file_path: str, line_number: int) -> str:
    """Reads specific file content around a target line number.
    Returns 300 lines centered on the requested line.
    Use relative paths from the project root.
    Avoid exploratory reading - use only when you know exactly what to examine."""
    return _run_tool("readFile", file_path=file_path, line_number=line_number)


@mcp.tool()
def getFileStructure(dir: str = ".") -> str:
    """Returns project directory structure as a tree.
    Useful for understanding project layout and locating files.
    Defaults to root directory if not specified."""
    return _run_tool("getFileStructure", dir=dir)


@mcp.tool()
def getClassHierarchy(class_qualified_name: str) -> str:
    """Retrieves class hierarchy and inheritance patterns.
    Returns superclasses and subclasses for the specified class.
    Use fully qualified class name (e.g., 'django.views.View')."""
    return _run_tool("getClassHierarchy", class_qualified_name=class_qualified_name)


@mcp.tool()
def getPackageDependencies(root_package: str) -> str:
    """Shows hierarchical relationships between modules and sub-packages.
    Returns imports and imported-by relationships for the package.
    Use top-level package name (e.g., 'django', 'langchain')."""
    return _run_tool("getPackageDependencies", root_package=root_package)


@mcp.tool()
def getControlFlowGraph() -> str:
    """Retrieves complete project control flow graph (CFG) showing all method calls.
    Primary analysis tool - use this first to understand project execution flow.
    Provides graphical representation of function/method relationships.
    Essential data - analyze this output thoroughly before using other tools."""
    return _run_tool("getControlFlowGraph")


@mcp.tool()
def getMethodInvocations(method: str) -> str:
    """Retrieves method invocation relationships from CFG.
    Returns list of methods that call or are called by the specified method.
    Use fully qualified method name."""
    return _run_tool("getMethodInvocations", method=method)


@mcp.tool()
def readDocs(file_path: str = "README.md", line_number: int = 1) -> str:
    """Reads project documentation files (.md, .rst, .txt, .html).
    Defaults to README.md if not specified.
    Returns 300 lines centered on the requested line number."""
    return _run_tool("readDocs", file_path=file_path, line_number=line_number)


@mcp.tool()
def readExternalDeps() -> str:
    """Scans repository for common dependency manifest files.
    Returns list of discovered dependency files (requirements.txt, pyproject.toml, etc.)."""
    return _run_tool("readExternalDeps")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
