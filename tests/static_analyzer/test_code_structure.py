import unittest
from pathlib import Path

from agents.tools.base import RepoContext
from agents.tools.read_structure import CodeStructureTool
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramEdge, ProgramEdgeKind, ProgramGraph
from tests.program_graph_factory import make_symbol

# The hierarchy the tool reads is a projection of CLASS nodes + INHERITS edges,
# so fixtures declare the classes and let ``ProgramGraph.hierarchy()`` derive
# super/subclass lists.
PYTHON_CLASSES = [
    ("myapp.models.User", "myapp/models.py", 10, 50),
    ("myapp.models.BaseModel", "myapp/base.py", 5, 20),
    ("myapp.models.UserMixin", "myapp/mixins.py", 1, 4),
    ("myapp.models.AdminUser", "myapp/admin.py", 1, 4),
    ("myapp.models.GuestUser", "myapp/guest.py", 1, 4),
    ("myapp.models.Product", "myapp/product.py", 1, 4),
]
PYTHON_INHERITS = [
    ("myapp.models.User", "myapp.models.BaseModel"),
    ("myapp.models.User", "myapp.models.UserMixin"),
    ("myapp.models.AdminUser", "myapp.models.User"),
    ("myapp.models.GuestUser", "myapp.models.User"),
    ("myapp.models.Product", "myapp.models.BaseModel"),
]


def _class_graph(
    language: str,
    classes: list[tuple[str, str, int, int]],
    inherits: list[tuple[str, str]],
) -> ProgramGraph:
    graph = ProgramGraph(language=language)
    for qname, file_path, line_start, line_end in classes:
        graph.add_node(make_symbol(qname, NodeType.CLASS, file_path, line_start, line_end, language=language))
    for source, target in inherits:
        graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, source, target))
    return graph


class TestCodeStructureTool(unittest.TestCase):
    def setUp(self):
        self.static_analysis = StaticAnalysisResults()
        self.static_analysis.add_program_graph(Language.PYTHON, _class_graph("python", PYTHON_CLASSES, PYTHON_INHERITS))
        ignore_manager = RepoIgnoreManager(Path("."))
        context = RepoContext(repo_dir=Path("."), ignore_manager=ignore_manager, static_analysis=self.static_analysis)
        self.tool = CodeStructureTool(context=context)

    def test_get_class_hierarchy(self):
        result = self.tool._run("myapp.models.User")
        self.assertIn("myapp.models.User", result)
        self.assertIn("BaseModel", result)
        self.assertIn("UserMixin", result)
        self.assertIn("AdminUser", result)
        self.assertIn("GuestUser", result)

    def test_get_base_class(self):
        # A class with no superclasses still reports the classes below it.
        result = self.tool._run("myapp.models.BaseModel")
        self.assertIn("myapp.models.BaseModel", result)
        self.assertIn("User", result)
        self.assertIn("Product", result)

    def test_class_not_found(self):
        result = self.tool._run("myapp.models.NonExistent")
        self.assertIn("No class hierarchy found", result)
        self.assertIn("myapp.models.NonExistent", result)
        self.assertIn("getSourceCode", result)

    def test_multiple_languages(self):
        self.static_analysis.add_program_graph(
            Language.TYPESCRIPT,
            _class_graph(
                "typescript",
                [
                    ("src.controllers.UserController", "src/controllers.ts", 15, 45),
                    ("src.controllers.BaseController", "src/base.ts", 1, 10),
                ],
                [("src.controllers.UserController", "src.controllers.BaseController")],
            ),
        )

        result = self.tool._run("src.controllers.UserController")
        self.assertIn("UserController", result)
        self.assertIn("BaseController", result)

    def test_no_static_analysis(self):
        context = RepoContext(repo_dir=Path("."), ignore_manager=RepoIgnoreManager(Path(".")), static_analysis=None)
        tool = CodeStructureTool(context=context)
        result = tool._run("myapp.models.User")
        self.assertIn("Error: Static analysis is not set", result)

    def test_case_sensitivity(self):
        result = self.tool._run("myapp.models.user")
        self.assertIn("No class hierarchy found", result)
