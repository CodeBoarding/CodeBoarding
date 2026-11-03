import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infra.repo_utils.ignore import RepoIgnoreManager
from infra.vscode_constants import VSCODE_CONFIG
from static_analyzer import _create_engine_configs
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.cpp_templates import complete_template_dependencies
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallSite, LanguageAnalysisResult
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
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


def test_cpp_warns_without_build_context(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "CMakeLists.txt").write_text("project(example LANGUAGES CXX)")
    CppAdapter().prepare_project(tmp_path)
    assert "using clangd defaults" in caplog.text
    assert not (tmp_path / "compile_commands.json").exists()


@pytest.mark.parametrize("severity", [1, 2])
def test_cpp_retains_parse_diagnostics(severity: int, caplog: pytest.LogCaptureFixture) -> None:
    client = MagicMock(spec=LSPClient)
    client.get_collected_diagnostics.return_value = {
        "kernel.cu": [LSPDiagnostic(message="CUDA header is unavailable", severity=severity)]
    }
    CppAdapter().wait_for_diagnostics(client)
    assert client.get_collected_diagnostics()["kernel.cu"][0].severity == severity
    if severity == 1:
        assert "analysis may be incomplete" in caplog.text


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


@pytest.mark.parametrize("database", ["compile_flags.txt", "compile_commands.json", "shared"])
def test_template_dependencies_follow_each_instantiation(
    tmp_path: Path, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project with spaces"
    root.mkdir()
    header = root / "api.hpp"
    source = root / "main.cpp"
    header_lines = [
        f"namespace {namespace} {{ struct Packet {{ friend int inspect(Packet) {{ return 1; }} }}; }}"
        for namespace in ("left", "right", "unused")
    ]
    header.write_text("\n".join(header_lines), encoding="utf-8")
    template = "template<class T> int forward(T value) { /* \U0001f600 */ return inspect(value); }"
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
    if database == "shared":
        shared = tmp_path / "analysis commands"
        shared.mkdir()
        (root / database).rename(shared / "compile_commands.json")
        (root / ".clangd").write_text("CompileFlags:\n  Add: [-include, unavailable.hpp]\n")
        monkeypatch.setenv("CODEBOARDING_CPP_COMPILATION_DATABASE", str(shared))
        command = CppAdapter().get_lsp_command(root)
        assert f"--compile-commands-dir={shared}" in command
        assert "--enable-config=false" in command
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


def test_cpp_rejects_missing_shared_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEBOARDING_CPP_COMPILATION_DATABASE", str(tmp_path / "absent"))
    with pytest.raises(WorkspaceSetupError, match="must name a directory"):
        CppAdapter().prepare_project(tmp_path)


@pytest.mark.parametrize("context", ["missing", "clangd", "bad-header"])
def test_partial_cpp_preserves_edges_and_reports_template_limitations(tmp_path: Path, context: str) -> None:
    source = tmp_path / "main.cpp"
    source.write_text('#include "missing.hpp"\nint run() { return missing(1); }\n')
    if context == "clangd":
        (tmp_path / ".clangd").write_text("CompileFlags:\n  Add: [-DCUSTOM]\n")
    elif context == "bad-header":
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
    adapter = CppAdapter()
    table = SymbolTable(adapter)
    position = {"line": 1, "character": 19}
    table.register_symbols(
        source,
        [
            {
                "name": "run",
                "kind": NodeType.FUNCTION,
                "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 40}},
                "selectionRange": {"start": {"line": 1, "character": 4}},
            }
        ],
        [],
        tmp_path,
    )
    client = MagicMock(spec=LSPClient)
    client.outgoing_calls.return_value = []
    client.document_ast.return_value = {"kind": "UnresolvedLookup", "range": {"start": position}}
    client.send_definition_batch.return_value = [[]]
    client.send_type_definition_batch.return_value = []
    ctx = EdgeBuildContext(client, table, SourceInspector())
    site = CallSite.from_lsp_position(str(source), 1, 4)
    edges = {("existing.caller", "existing.target"): [site]}
    adapter.complete_edges(ctx, [source], edges, tmp_path)
    assert edges == {("existing.caller", "existing.target"): [site]}
    result = convert_to_codeboarding_format(
        table, LanguageAnalysisResult(diagnostics=ctx.diagnostics), adapter, RepoIgnoreManager(tmp_path)
    )
    warning = result["diagnostics"][str(tmp_path)][0]
    assert warning.code == "cpp-template-analysis-incomplete"
    assert warning.severity == 2
    assert "Template-dependent calls may be missing" in warning.message


@pytest.mark.parametrize("skip", [False, True])
def test_optional_hierarchy_preserves_calls_and_reports_omissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skip: bool
) -> None:
    monkeypatch.setenv("CODEBOARDING_CPP_SKIP_TYPE_HIERARCHY", "1" if skip else "0")
    source = tmp_path / "main.cpp"
    lines = ["struct Base {};", "struct Child : Base {};", "int leaf() { return 7; }", "int run() { return leaf(); }"]
    source.write_text("\n".join(lines))
    symbols = []
    for line, name in enumerate(["Base", "Child", "leaf", "run"]):
        position = {"line": line, "character": lines[line].index(name)}
        symbols.append(
            {
                "name": name,
                "kind": NodeType.CLASS if line < 2 else NodeType.FUNCTION,
                "range": {
                    "start": {"line": line, "character": 0},
                    "end": {"line": line, "character": len(lines[line])},
                },
                "selectionRange": {"start": position, "end": position},
            }
        )
    client = MagicMock(spec=LSPClient)
    client.document_symbol.return_value = symbols
    client.document_ast.return_value = {}
    client.outgoing_calls.return_value = []
    client.send_definition_batch.side_effect = lambda queries, **_: [
        [{"uri": source.as_uri(), "range": symbols[2]["selectionRange"]}] for _ in queries
    ]
    client.send_implementation_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    client.send_type_definition_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    client.type_hierarchy_prepare.side_effect = lambda path, line, column: [
        {"name": symbols[line]["name"], "uri": source.as_uri(), "selectionRange": symbols[line]["selectionRange"]}
    ]
    client.type_hierarchy_supertypes.side_effect = lambda item: (
        [{"name": "Base", "uri": source.as_uri(), "selectionRange": symbols[0]["selectionRange"]}]
        if item["name"] == "Child"
        else []
    )
    client.type_hierarchy_subtypes.return_value = []
    result = CallGraphBuilder(client, CppAdapter(), tmp_path, tmp_path).build([source])
    assert {(edge.source, edge.destination) for edge in result.cfg.edges} == {("main.cpp.run", "main.cpp.leaf")}
    if skip:
        assert result.hierarchy == {}
        assert result.diagnostics[str(tmp_path)][0].code == "type-hierarchy-disabled"
    else:
        assert result.hierarchy["main.cpp.Child"]["superclasses"] == ["main.cpp.Base"]
        assert result.diagnostics == {}
