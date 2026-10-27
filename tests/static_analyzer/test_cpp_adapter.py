import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infra.repo_utils.ignore import RepoIgnoreManager
from infra.vscode_constants import VSCODE_CONFIG
from static_analyzer import _create_engine_configs
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter
from static_analyzer.engine.cpp_templates import complete_template_dependencies
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.exceptions import WorkspaceSetupError
from static_analyzer.lsp_client.diagnostics import LSPDiagnostic
from static_analyzer.programming_language import ProgrammingLanguageBuilder


@pytest.mark.parametrize(
    "detected",
    [
        [("C++", ".cpp"), ("CUDA", ".cu"), ("C++ Header", ".hpp"), ("C Header", ".h")],
        [("C++ Header", ".hpp"), ("C Header", ".h")],
    ],
)
def test_cpp_and_cuda_share_one_engine_and_discover_headers(tmp_path: Path, detected: list[tuple[str, str]]) -> None:
    names = ["host.cpp", "host.hpp", "device.cu", "device.cuh", "include/api.h"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("// source\n")
    builder = ProgrammingLanguageBuilder(VSCODE_CONFIG["lsp_servers"])
    languages = [builder.build(name, 100, 25, {suffix}) for name, suffix in detected]
    assert {language.lsp_server_key for language in languages} == {"cpp"}
    ignore = RepoIgnoreManager(tmp_path)
    configs = _create_engine_configs(languages, tmp_path, ignore)
    assert len(configs) == 1
    assert configs[0].adapter.language_enum == Language.CPP
    assert set(configs[0].adapter.discover_source_files(tmp_path, ignore)) == {tmp_path / name for name in names}


def test_cpp_requires_build_context(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("project(example LANGUAGES CXX)")
    with pytest.raises(WorkspaceSetupError, match="CMAKE_EXPORT_COMPILE_COMMANDS"):
        CppAdapter().prepare_project(tmp_path)


@pytest.mark.parametrize("severity", [1, 2])
def test_cpp_rejects_parse_errors_but_accepts_warnings(severity: int) -> None:
    client = MagicMock(spec=LSPClient)
    client.get_collected_diagnostics.return_value = {
        "kernel.cu": [LSPDiagnostic(message="CUDA header is unavailable", severity=severity)]
    }
    if severity == 1:
        with pytest.raises(WorkspaceSetupError, match="kernel.cu:1: CUDA header is unavailable"):
            CppAdapter().wait_for_diagnostics(client)
    else:
        CppAdapter().wait_for_diagnostics(client)


@pytest.mark.parametrize(
    "name", [".clangd", "compile_flags.txt", "compile_commands.json", "build/compile_commands.json"]
)
def test_cpp_preserves_explicit_configuration(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.parent.mkdir(exist_ok=True)
    contents = "CompileFlags:\n  CompilationDatabase: build/debug\n" if name == ".clangd" else "[]\n"
    path.write_text(contents)
    CppAdapter().prepare_project(tmp_path)
    assert path.read_text() == contents


def test_cpp_overloads_and_same_stem_header_survive_registration(tmp_path: Path) -> None:
    table = SymbolTable(CppAdapter())
    symbols = [
        {
            "name": "scale",
            "kind": NodeType.FUNCTION,
            "detail": detail,
            "range": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 30}},
            "selectionRange": {"start": {"line": line, "character": 4}},
        }
        for line, detail in enumerate(["int (int)", "double (double)"])
    ]
    for name in ("math.cpp", "math.hpp"):
        table.register_symbols(tmp_path / name, symbols, [], tmp_path)
    assert set(table.symbols) == {
        "math.cpp.scale(int)",
        "math.cpp.scale(double)",
        "math.hpp.scale(int)",
        "math.hpp.scale(double)",
    }


@pytest.mark.parametrize("database", ["compile_flags.txt", "compile_commands.json"])
def test_template_dependencies_follow_each_instantiation(tmp_path: Path, database: str) -> None:
    root = tmp_path / "project with spaces"
    root.mkdir()
    header = root / "api.hpp"
    source = root / "main.cpp"
    header_lines = [
        f"namespace {namespace} {{ struct Packet {{ friend int inspect(Packet) {{ return 1; }} }}; }}"
        for namespace in ("left", "right", "unused")
    ]
    header.write_text("\n".join(header_lines), encoding="utf-8")
    template = "template<class T> int forward(T value) { /* 😀 */ return inspect(value); }"
    source.write_text(
        '#include "api.hpp"\n'
        + template
        + "\ntemplate<class T> int nested(T value) { return forward(value); }\n"
        + "int run() { return nested(left::Packet{}) + nested(right::Packet{}); }\n",
        encoding="utf-8",
    )
    flags = ["-std=c++20", "-I."]
    (root / database).write_text(
        (
            "\n".join(flags)
            if database == "compile_flags.txt"
            else json.dumps(
                [{"directory": str(root), "file": "main.cpp", "arguments": ["clang++", *flags, "main.cpp"]}]
            )
        ),
        encoding="utf-8",
    )
    table = SymbolTable(CppAdapter())
    for path, declarations in (
        (
            header,
            [
                (namespace, line, text, "inspect")
                for line, (namespace, text) in enumerate(zip(("left", "right", "unused"), header_lines))
            ],
        ),
        (source, [("forward", 1, template, "forward")]),
    ):
        table.register_symbols(
            path,
            [
                {
                    "name": name,
                    "kind": NodeType.FUNCTION,
                    "range": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": len(text)}},
                    "selectionRange": {"start": {"line": line, "character": text.index(spelling)}},
                }
                for name, line, text, spelling in declarations
            ],
            [],
            root,
        )
    column = len(template[: template.index("inspect")].encode("utf-16-le")) // 2
    edges: dict[tuple[str, str], list[CallSite]] = {}
    ctx = EdgeBuildContext(MagicMock(spec=LSPClient), table, SourceInspector())
    complete_template_dependencies(ctx, [header, source], root, {(str(source), 1, column)}, edges)
    assert set(edges) == {("main.cpp.forward", "api.hpp.left"), ("main.cpp.forward", "api.hpp.right")}
    assert all(sites == [CallSite.from_lsp_position(str(source), 1, column)] for sites in edges.values())
