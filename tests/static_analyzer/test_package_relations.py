import unittest
from pathlib import Path

from agents.tools.base import RepoContext
from agents.tools.read_packages import PackageRelationsTool
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    file_node_id,
    package_node_id,
)

# Package relations are a projection of PACKAGE/FILE nodes joined by CONTAINS and
# IMPORTS edges. Only internal packages are projected -- external ones are kept in
# the graph but deliberately excluded from this view -- so fixtures wire
# package-to-package imports through the files that declare them.


def _package_graph(language: str, packages: dict[str, str], imports: list[tuple[str, str]]) -> ProgramGraph:
    """Build a graph from {package: file} plus (importing_package, imported_package) pairs."""
    graph = ProgramGraph(language=language)
    for package, file_path in packages.items():
        graph.add_node(ProgramNode(package_node_id(language, package), ProgramNodeKind.PACKAGE, language, package))
        graph.add_node(ProgramNode(file_node_id(file_path), ProgramNodeKind.FILE, language, file_path, file_path))
        graph.add_edge(
            ProgramEdge(ProgramEdgeKind.CONTAINS, package_node_id(language, package), file_node_id(file_path))
        )
    for source, target in imports:
        graph.add_edge(
            ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id(packages[source]), file_node_id(packages[target]))
        )
    return graph


class TestPackageRelationsTool(unittest.TestCase):
    def setUp(self):
        self.static_analysis = StaticAnalysisResults()
        self.static_analysis.add_program_graph(
            Language.PYTHON,
            _package_graph(
                "python",
                {"mypackage": "mypackage/mod.py", "utils": "utils/helper.py", "main": "main/app.py"},
                [("mypackage", "utils"), ("main", "mypackage")],
            ),
        )
        ignore_manager = RepoIgnoreManager(Path("."))
        context = RepoContext(repo_dir=Path("."), ignore_manager=ignore_manager, static_analysis=self.static_analysis)
        self.tool = PackageRelationsTool(context=context)

    def test_get_package_dependencies(self):
        result = self.tool._run("mypackage")
        self.assertIn("mypackage", result)
        self.assertIn("utils", result)
        self.assertIn("main", result)

    def test_get_utils_package(self):
        result = self.tool._run("utils")
        self.assertIn("utils", result)
        self.assertIn("mypackage", result)

    def test_package_not_found(self):
        result = self.tool._run("nonexistent")
        self.assertIn("No package relations found", result)
        self.assertIn("nonexistent", result)

    def test_multiple_languages(self):
        self.static_analysis.add_program_graph(
            Language.TYPESCRIPT,
            _package_graph(
                "typescript",
                {"src": "src/index.ts", "lib": "lib/util.ts"},
                [("src", "lib")],
            ),
        )

        result = self.tool._run("src")
        self.assertIn("src", result)
        self.assertIn("lib", result)

    def test_no_static_analyzer(self):
        context = RepoContext(repo_dir=Path("."), ignore_manager=RepoIgnoreManager(Path(".")), static_analysis=None)
        tool = PackageRelationsTool(context=context)
        result = tool._run("mypackage")
        self.assertIn("Error: Static analysis is not set", result)

    def test_package_list_in_error(self):
        # The miss message lists the packages that were available instead.
        result = self.tool._run("badpackage")
        self.assertIn("No package relations found", result)
        self.assertIn("mypackage", result)
