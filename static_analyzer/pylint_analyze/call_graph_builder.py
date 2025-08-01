import logging
import os
from pathlib import Path
from typing import Set

from astroid import InferenceError, nodes, Uninferable, MANAGER


class DiGraph:
    def __init__(self):
        self.nodes = set()
        self.edges = dict()  # key: src, value: list of (dst, data)

    def add_node(self, node):
        self.nodes.add(node)

    def add_edge(self, src, dst, **data):
        self.add_node(src)
        self.add_node(dst)
        self.edges.setdefault(src, []).append((dst, data))

    def number_of_nodes(self):
        return len(self.nodes)

    def number_of_edges(self):
        return sum(len(edges) for edges in self.edges.values())

    def to_node_link_data(self):
        # Convert to format similar to networkx.node_link_data
        nodes = [{"id": n} for n in self.nodes]
        links = [
            {"source": src, "target": dst, **attrs}
            for src, dsts in self.edges.items()
            for dst, attrs in dsts
        ]
        return {"directed": True, "multigraph": False, "nodes": nodes, "links": links}


def _expr_to_str(expr: nodes.NodeNG) -> str:
    """
    Best‑effort pretty representation of an astroid expression.
    Falls back to `as_string()` if the value cannot be folded.
    """
    try:
        inferred = next(expr.infer())
        if inferred is not Uninferable and isinstance(inferred, nodes.Const):
            # a real constant – print its Python value
            return repr(inferred.value)
    except (InferenceError, StopIteration, AttributeError):
        # AttributeError covers the case "expr" is not a NodeNG at all
        pass

    # astroid >= 2.15 always implements as_string()
    try:
        return expr.as_string()
    except Exception:  # pragma: no cover
        return str(expr)


def _collect_arguments(call: nodes.Call) -> tuple[list[str], dict[str, str]]:
    """
    Return two objects describing what was literally written at the
    call‑site.
      • positional arguments (order is preserved)
      • keyword arguments  (mapping name -> expression)

    Works with astroid 2.x *and* 3.x.
    """
    positional: list[str] = []
    keywords: dict[str, str] = {}

    # ---------- positional ---------------------------------
    for arg in call.args:  # astroid 3.x
        if isinstance(arg, nodes.Starred):  # *argument
            positional.append('*' + _expr_to_str(arg.value))
        else:
            positional.append(_expr_to_str(arg))

    # ---------- keyword ------------------------------------
    for kw in call.keywords or []:  # works for 2.x & 3.x
        if kw.arg is None:  # **kwargs expression
            keywords['**'] = _expr_to_str(kw.value)
        else:  # normal name=value
            keywords[kw.arg] = _expr_to_str(kw.value)

    return positional, keywords


class CallGraphBuilder:
    """
    A very small, purely static, intra‑/inter module call‑graph builder.
    Limitations
    -----------
    • Dynamic calls (getattr, eval, reflection, etc.) are shown as <dynamic>.
    • Resolution of aliases and from‑import renames is shallow on purpose.
    Still good enough to grasp the overall call flow.
    """

    def __init__(self, root: Path, max_depth: int | None = None):
        self.root = root
        self.graph: DiGraph = DiGraph()
        self._visited_files: Set[Path] = set()
        self.max_depth = max_depth

    def build(self) -> DiGraph:
        logging.info(f"[CallGraphBuilder] Building ASTs")
        exclude_dirs = {"test", "tests", "testing", "examples", "__pycache__", ".venv", ".git", ".tox"}
        for pyfile in self._iter_py_files():
            skip = False
            for exluded_dir in exclude_dirs:
                if exluded_dir in pyfile.parts:
                    skip = True
                    break
            if skip:
                continue
            self._process_file(pyfile)

        logging.info(f"[CallGraphBuilder] " +
                     f"Call‑graph built: {self.graph.number_of_nodes()} nodes," +
                     f" {self.graph.number_of_edges()} edges.")
        return self.graph

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
            logging.warning(f"!! Failed to parse {file_path}")
            return

        self._visited_files.add(file_path)

        # Avoid circular imports by using the module name as a prefix
        module_qualname = module.name

        # Custom patch if the module is just the path to the file
        if str(self.root) in module_qualname:
            module_qualname = module.name.split(str(self.root))[1].replace("/", ".")
            if module_qualname.startswith("."):
                module_qualname = module_qualname[1:]
            if module_qualname.endswith(".py"):
                module_qualname = module_qualname[:-3]

        self._visit_module(module, module_qualname)

    def _visit_module(self, module: nodes.Module, module_qname: str):
        for node in module.body:
            if isinstance(node, (nodes.FunctionDef, nodes.AsyncFunctionDef)):
                self._visit_function(node, module_qname)
            elif isinstance(node, nodes.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (nodes.FunctionDef, nodes.AsyncFunctionDef)):
                        self._visit_function(sub, f"{module_qname}.{node.name}")

    def _qual_name(self, func: nodes.FunctionDef | nodes.AsyncFunctionDef, owner: str) -> str:
        return f"{owner}.{func.name}"
        # return f"{owner}:{func.name}@{func.lineno}"

    def _visit_function(
            self, func: nodes.FunctionDef | nodes.AsyncFunctionDef, owner: str
    ):
        src = self._qual_name(func, owner)
        self.graph.add_node(src)

        for call in func.nodes_of_class(nodes.Call):
            callee_label = self._resolve_callee(call)

            pos_args, kw_args = _collect_arguments(call)

            # one edge per call site, keep line number to distinguish calls
            dst = f"{callee_label}"  # @{call.lineno}"

            # Handle dynamic calls
            self.graph.add_edge(
                self.fix_reference_name(src),
                self.fix_reference_name(dst),
                pos_args=pos_args,
                kw_args=kw_args,
                lineno=call.lineno,
            )

    @staticmethod
    def _resolve_callee(call: nodes.Call) -> str:
        """
        Try to obtain a printable name for the call target.
        """
        func = call.func
        try:
            inferred = next(func.infer(), None)
            if inferred is not None:
                return inferred.qname()
        except (InferenceError, StopIteration):
            pass

        # Fallback for dynamic or unresolved calls
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

    def write_dot(self, filename: Path):
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('digraph G {\n')
            for src, dsts in self.graph.edges.items():
                for dst, attrs in dsts:
                    label = f" [label=\"{attrs.get('lineno', '')}\"]"
                    f.write(f'  "{src}" -> "{dst}"{label};\n')
            f.write('}\n')

    @staticmethod
    def fix_reference_name(qualified_name):
        if "repos" in qualified_name:
            qualified_name = qualified_name.split("repos")[1]
            if qualified_name.startswith(".") or qualified_name.startswith("/"):
                qualified_name = qualified_name[1:]
        if "/" in qualified_name:
            # Check if there is .py
            qualified_name = "".join(qualified_name.split(".py"))
            qualified_name = ".".join(qualified_name.split("/"))
        return qualified_name
