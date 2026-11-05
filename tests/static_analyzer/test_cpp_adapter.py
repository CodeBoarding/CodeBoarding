import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infra.repo_utils.ignore import RepoIgnoreManager
from infra.vscode_constants import VSCODE_CONFIG
from static_analyzer import StaticAnalyzer, _create_engine_configs
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.cpp_templates import complete_template_dependencies
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.lsp_client import LSPClient, MethodNotFoundError
from static_analyzer.engine.models import CallSite, LanguageAnalysisResult
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.exceptions import StaticAnalysisFatalError
from static_analyzer.lsp_client.diagnostics import LSPDiagnostic
from static_analyzer.programming_language import ProgrammingLanguageBuilder
from static_analyzer.scanner import ProjectScanner


@pytest.mark.parametrize(
    "detected",
    [
        [("C++", ".cpp"), ("CUDA", ".cu"), ("C++ Header", ".hpp"), ("C Header", ".h")],
        [("C++ Header", ".hpp"), ("C Header", ".h")],
        [("C++ Header", ".hxx")],
        [("C++ Header", ".hh")],
        [("CUDA", ".cuh")],
        [("C++", ".cc")],
        [("C++", ".cxx")],
    ],
)
def test_cpp_and_cuda_share_one_engine_and_discover_headers(tmp_path: Path, detected: list[tuple[str, str]]) -> None:
    names = [f"source{suffix}" for _, suffix in detected] + ["include/api.h"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("// source\n")
    builder = ProgrammingLanguageBuilder(VSCODE_CONFIG["lsp_servers"])
    languages = [builder.build(name, 100, 25, {suffix}) for name, suffix in detected]
    assert {language.lsp_server_key for language in languages if language.is_supported_lang()} == {"cpp"}
    ignore = RepoIgnoreManager(tmp_path)
    configs = _create_engine_configs(languages, tmp_path, ignore)
    assert len(configs) == 1
    assert configs[0].adapter.language_enum == Language.CPP
    assert set(configs[0].adapter.discover_source_files(tmp_path, ignore)) == {tmp_path / name for name in names}


@pytest.mark.parametrize("primary", ["Python", "C", "C Header"])
def test_c_headers_do_not_select_cpp(tmp_path: Path, primary: str) -> None:
    builder = ProgrammingLanguageBuilder(VSCODE_CONFIG["lsp_servers"])
    header = builder.build("C Header", 10, 10, {".h"})
    assert not header.is_supported_lang()
    assert header.lsp_server_key != "cpp"
    suffix = {"Python": ".py", "C": ".c", "C Header": ".h"}[primary]
    languages = [header, builder.build(primary, 90, 90, {suffix})]
    configs = _create_engine_configs(languages, tmp_path, RepoIgnoreManager(tmp_path))
    assert [config.adapter.language_enum for config in configs] == ([Language.PYTHON] if primary == "Python" else [])


@pytest.mark.parametrize("folder", ["include", "build", ".hidden", "tests"])
def test_cuda_header_only_detection_respects_exclusions(tmp_path: Path, folder: str) -> None:
    header = tmp_path / folder / "api.cuh"
    header.parent.mkdir()
    header.write_text("__device__ int run() { return 1; }\n")
    with patch("static_analyzer.scanner.subprocess.run", return_value=MagicMock(stdout='{"Total":{"code":0}}')):
        languages = ProjectScanner(tmp_path).scan()
    assert [language.lsp_server_key for language in languages] == (["cpp"] if folder == "include" else [])


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


@pytest.mark.parametrize("asts", [[None, None], [None, {}], [{}, None], [{}, {}]])
def test_ast_limitations_survive_both_analysis_phases(tmp_path: Path, asts: list) -> None:
    source = tmp_path / "main.cpp"
    text = "int run() { auto work = [] { return 7; }; return work(); }"
    source.write_text(text)
    client = MagicMock(spec=LSPClient)
    client.document_symbol.return_value = [
        {
            "name": "run",
            "kind": NodeType.FUNCTION,
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": len(text)}},
            "selectionRange": {"start": {"line": 0, "character": 4}},
        }
    ]
    client.document_ast.side_effect = asts
    client.outgoing_calls_batch.return_value = []
    client.symbol_info_batch.return_value = []
    client.send_definition_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    client.send_implementation_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    client.get_collected_diagnostics.return_value = {str(source): []}
    builder = CallGraphBuilder(client, CppAdapter(), tmp_path, tmp_path)
    result = builder.build([source])
    assert result.references
    if None in asts:
        warnings = result.diagnostics[str(source)]
        assert len(warnings) == 1
        assert warnings[0].code == "cpp-ast-unavailable"
        assert warnings[0].severity == 2
        assert "may be missing" in warnings[0].message
    else:
        assert result.diagnostics == {}

    expected = result.diagnostics.get(str(source), [])
    analysis = {"diagnostics": {str(source): [*expected, LSPDiagnostic(message="stale compiler warning")]}}
    analyzer = object.__new__(StaticAnalyzer)
    analyzer.collected_diagnostics = {}
    analyzer._collect_diagnostics_for(CppAdapter(), client, analysis)
    assert analyzer.collected_diagnostics[Language.CPP][str(source)] == expected


@pytest.mark.parametrize("unsupported", ["textDocument/prepareCallHierarchy", "callHierarchy/outgoingCalls"])
def test_unsupported_call_hierarchy_reports_clangd_upgrade(tmp_path: Path, unsupported: str) -> None:
    source = tmp_path / "main.cpp"
    source.write_text("int run() { return 1; }")
    adapter = CppAdapter()
    table = SymbolTable(adapter)
    table.register_symbols(
        source,
        [{"name": "run", "kind": NodeType.FUNCTION, "range": {"start": {"line": 0, "character": 4}}}],
        [],
        tmp_path,
    )
    client = LSPClient(["clangd"], tmp_path)

    def respond(message):
        response = {"id": message["id"]}
        if message["method"] == unsupported:
            response["error"] = {"code": -32601, "message": "Method not found"}
        else:
            response["result"] = [{"name": "run"}]
        client._msg_queue.put(response)

    with patch.object(client, "_write_message", side_effect=respond):
        with pytest.raises(StaticAnalysisFatalError, match="clangd 20 or newer") as failure:
            adapter.complete_edges(EdgeBuildContext(client, table, SourceInspector()), [source], {}, tmp_path)
    assert isinstance(failure.value.__cause__, MethodNotFoundError)
    assert "Upgrade" in str(failure.value)


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


@pytest.mark.parametrize("database", ["compile_flags.txt", "compile_commands.json", "build/compile_commands.json"])
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
    template = "template<class T> int forward(T value) { /* \U0001f600 */ return inspect(value); }"
    source.write_text(
        '#include "api.hpp"\n'
        + template
        + "\ntemplate<class T> int nested(T value) { return forward(value); }\n"
        + "int run() { return nested(left::Packet{}) + nested(right::Packet{}); }\n",
        encoding="utf-8",
    )
    flags = ["-std=c++20", "-I."]
    (root / database).parent.mkdir(exist_ok=True)
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
    client.outgoing_calls_batch.return_value = []
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


@pytest.mark.parametrize("suffix", ["cpp", "cu"])
def test_cpp_resolves_concrete_bases_without_guessing_dependent_bases(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"main.{suffix}"
    lines = [
        "struct Base {};",
        "template<class T> struct Child : public Base, T {};",
        "int leaf() { return 7; }",
        "int run() { return leaf(); }",
    ]
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
    client.outgoing_calls_batch.return_value = []
    client.send_definition_batch.side_effect = lambda queries, **_: [
        [{"uri": source.as_uri(), "range": symbols[2]["selectionRange"]}] for _ in queries
    ]
    client.send_implementation_batch.side_effect = lambda queries, **_: [[] for _ in queries]
    client.send_type_definition_batch.side_effect = lambda queries, **_: [
        (
            [{"uri": source.as_uri(), "range": symbols[0]["selectionRange"]}]
            if line == 1 and column == lines[1].index("Base")
            else []
        )
        for _, line, column in queries
    ]
    client.type_hierarchy_prepare.return_value = []
    result = CallGraphBuilder(client, CppAdapter(), tmp_path, tmp_path).build([source])
    assert {(edge.source, edge.destination) for edge in result.cfg.edges} == {
        (f"main.{suffix}.run", f"main.{suffix}.leaf")
    }
    assert result.hierarchy[f"main.{suffix}.Child"]["superclasses"] == [f"main.{suffix}.Base"]
    assert result.hierarchy[f"main.{suffix}.Base"]["subclasses"] == [f"main.{suffix}.Child"]
    assert result.diagnostics == {}
