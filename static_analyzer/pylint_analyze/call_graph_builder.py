import json
import os
from pathlib import Path
from typing import Set

import networkx as nx
from astroid import MANAGER, nodes

from static_analyzer.pylint_analyze import _banner


class CallGraphBuilder:
    """
    A very small, purely static, intra‑/inter module call‑graph builder.
    Limitations
    -----------
    • Dynamic calls (getattr, eval, reflection, etc.) are shown as <dynamic>.
    • Resolution of aliases and from‑import renames is shallow on purpose.
    Still good enough to grasp the overall call flow.
    """

    def __init__(self, root: Path, max_depth: int | None = None, verbose: bool = False):
        self.root = root
        self.graph: nx.DiGraph = nx.DiGraph()
        self._visited_files: Set[Path] = set()
        self.max_depth = max_depth
        self.verbose = verbose

    # ──────────────────── public API ────────────────────────
    def build(self) -> nx.DiGraph:
        _banner("Building ASTs…", self.verbose)
        for pyfile in self._iter_py_files():
            self._process_file(pyfile)

        _banner(
            f"Call‑graph built: {self.graph.number_of_nodes()} nodes,"
            f" {self.graph.number_of_edges()} edges.",
            self.verbose,
        )
        return self.graph

    # ──────────────────── helpers ────────────────────────
    def _iter_py_files(self):
        base_depth = len(self.root.parts)
        for path, _, files in os.walk(self.root):
            path = Path(path)
            if self.max_depth is not None and (len(path.parts) - base_depth) > self.max_depth:
                continue
            for f in files:
                if f.endswith(".py") and not f.startswith("."):
                    yield path / f

    def _process_file(self, file_path: Path):
        if file_path in self._visited_files:
            return
        try:
            module = MANAGER.ast_from_file(str(file_path))
        except Exception as e:  # pylint: disable=broad-except
            print(f"!! Failed to parse {file_path}: {e}")
            _banner(f"!! Failed to parse {file_path}", self.verbose)
            _banner(traceback.format_exc(), self.verbose)
            return

        self._visited_files.add(file_path)

        module_qualname = module.name  # dotted import path if resolvable
        self._visit_module(module, module_qualname)

    # -------------------- AST walk ------------------------
    def _visit_module(self, module: nodes.Module, module_qname: str):
        for node in module.body:
            if isinstance(node, (nodes.FunctionDef, nodes.AsyncFunctionDef)):
                self._visit_function(node, module_qname)
            elif isinstance(node, nodes.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (nodes.FunctionDef, nodes.AsyncFunctionDef)):
                        self._visit_function(
                            sub, f"{module_qname}.{node.name}"
                        )

    def _qual_name(self, func: nodes.FunctionDef | nodes.AsyncFunctionDef, owner: str) -> str:
        return f"{owner}:{func.name}@{func.lineno}"

    def _visit_function(
            self, func: nodes.FunctionDef | nodes.AsyncFunctionDef, owner: str
    ):
        src = self._qual_name(func, owner)
        self.graph.add_node(src)

        for call in func.nodes_of_class(nodes.Call):
            callee_label = self._resolve_callee(call)
            self.graph.add_edge(src, callee_label)

    # -------------------- call target resolution ------------------------
    @staticmethod
    def _resolve_callee(call: nodes.Call) -> str:
        """
        Try to obtain a printable name for the call target.
        """
        func = call.func
        if isinstance(func, nodes.Attribute):
            base = CallGraphBuilder._as_string(func.expr)
            return f"{base}.{func.attrname}"
        if isinstance(func, nodes.Name):
            return func.name
        return "<dynamic>"

    @staticmethod
    def _as_string(expr: nodes.NodeNG) -> str:  # trivial pretty‑printer
        if isinstance(expr, nodes.Name):
            return expr.name
        if isinstance(expr, nodes.Attribute):
            return f"{CallGraphBuilder._as_string(expr.expr)}.{expr.attrname}"
        return "<?>"

    # ──────────────────── serialisation ────────────────────────
    def write_dot(self, filename: Path):
        from networkx.drawing.nx_agraph import write_dot  # Lazy import

        write_dot(self.graph, filename)

    def write_json(self, filename: Path):
        data = nx.node_link_data(self.graph)
        filename.write_text(json.dumps(data, indent=2), encoding="utf-8")
