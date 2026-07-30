import ast
from pathlib import Path


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_program_info_does_not_cross_forbidden_architecture_boundaries():
    forbidden = ("agents", "codeboarding_workflows", "output_generators", "monitoring", "health", "codeboarding_cli")
    for path in Path("static_analyzer/program_info").glob("*.py"):
        assert not any(module.startswith(forbidden) for module in imports(path)), path


def test_infomap_import_is_contained_to_program_map_backend():
    importers = []
    for root in (Path("static_analyzer"), Path("agents"), Path("diagram_analysis")):
        for path in root.rglob("*.py"):
            if "infomap" in imports(path):
                importers.append(path.as_posix())
    assert importers == ["static_analyzer/cluster_helpers.py"]


def test_internal_program_info_importers_are_designated_seams():
    importers = []
    for path in Path("static_analyzer").rglob("*.py"):
        if "program_info" in path.parts:
            continue
        if any(module.startswith("static_analyzer.program_info") for module in imports(path)):
            importers.append(path.as_posix())
    assert sorted(importers) == [
        "static_analyzer/analysis_result.py",
        "static_analyzer/cluster_helpers.py",
        "static_analyzer/engine/call_graph_builder.py",
        "static_analyzer/engine/models.py",
        "static_analyzer/engine/source_inspector.py",
        "static_analyzer/graph.py",
    ]
