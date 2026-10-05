from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import install
from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.dart_adapter import DartAdapter
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.models import CallSite
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.graph_definitions import GraphIndex, MEMBER_READ, MEMBER_WRITE, targets_for
from tool_registry.manifest import has_required_tools, resolve_config_from_path
from tool_registry.paths import dart_binary
from tool_registry.registry import TOOL_REGISTRY


@pytest.mark.parametrize("system,suffix", [("Linux", ""), ("Darwin", ""), ("Windows", ".exe")])
def test_prefers_flutter_sdk_over_unrelated_dart(tmp_path: Path, system: str, suffix: str):
    flutter = tmp_path / "Flutter SDK" / "bin" / "flutter"
    bundled = flutter.parent / "cache" / "dart-sdk" / "bin" / f"dart{suffix}"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    bundled.chmod(0o755)
    with (
        patch("tool_registry.paths.platform.system", return_value=system),
        patch(
            "tool_registry.paths.shutil.which",
            side_effect=lambda name: str(flutter) if name == "flutter" else "/other/dart",
        ),
    ):
        assert dart_binary() == str(bundled)


def test_standalone_sdk_and_missing_sdk(tmp_path: Path):
    with patch("tool_registry.paths.shutil.which", side_effect=lambda name: "/sdk/dart" if name == "dart" else None):
        assert DartAdapter().get_lsp_command(tmp_path) == ["/sdk/dart", "language-server"]
    with patch("tool_registry.paths.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="Dart SDK not found"):
            DartAdapter().get_lsp_command(tmp_path)


def test_windows_dart_wrapper_requires_bundled_executable(tmp_path: Path):
    wrapper = tmp_path / "bin" / "dart.bat"
    binary = wrapper.parent / "cache" / "dart-sdk" / "bin" / "dart.exe"
    with patch("tool_registry.paths.shutil.which", side_effect=lambda name: str(wrapper) if name == "dart" else None):
        assert dart_binary() is None
        binary.parent.mkdir(parents=True)
        binary.touch()
        binary.chmod(0o755)
        assert dart_binary() == str(binary)


def test_sdk_setup_reports_missing_without_reinstall_loop(tmp_path: Path):
    dart = next(dep for dep in TOOL_REGISTRY if dep.key == "dart")
    with (
        patch("tool_registry.manifest.TOOL_REGISTRY", [dart]),
        patch("tool_registry.paths.shutil.which", return_value=None),
        patch("install.TOOL_REGISTRY", [dart]),
    ):
        assert has_required_tools(tmp_path)
        check = install._language_checks_from_registry(tmp_path)[0]
        available, reason = check.evaluate(npm_available=False)
        assert not available
        assert reason and "Flutter SDK" in reason
    with patch("tool_registry.manifest.dart_binary", return_value="/sdk/dart"):
        assert resolve_config_from_path()["lsp_servers"]["dart"]["command"] == ["/sdk/dart", "language-server"]


@pytest.mark.parametrize("flutter", [False, True])
def test_restores_project_with_matching_sdk(tmp_path: Path, flutter: bool):
    manifest = "name: sample\n"
    if flutter:
        manifest += "dependencies:\n  flutter:\n    sdk: flutter\n"
    (tmp_path / "pubspec.yaml").write_text(manifest)
    ignored = tmp_path / ".dart_tool"
    ignored.mkdir()
    (ignored / "pubspec.yaml").write_text(manifest)
    with (
        patch("static_analyzer.engine.adapters.dart_adapter.dart_binary", return_value="/sdk/dart"),
        patch("static_analyzer.engine.adapters.dart_adapter.shutil.which", return_value="/sdk/flutter"),
        patch("static_analyzer.engine.adapters.dart_adapter.subprocess.run") as run,
    ):
        DartAdapter().prepare_project(tmp_path)
    run.assert_called_once_with(
        ["/sdk/flutter" if flutter else "/sdk/dart", "pub", "get"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


def test_flutter_project_requires_flutter_even_with_dart_installed(tmp_path: Path):
    (tmp_path / "pubspec.yaml").write_text("dev_dependencies:\n  flutter_test:\n    sdk: flutter\n")
    with (
        patch("static_analyzer.engine.adapters.dart_adapter.dart_binary", return_value="/sdk/dart"),
        patch("static_analyzer.engine.adapters.dart_adapter.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="Flutter SDK required"),
    ):
        DartAdapter().prepare_project(tmp_path)


def test_restore_failure_keeps_cause(tmp_path: Path):
    (tmp_path / "pubspec.yaml").write_text("name: broken\n")
    error = subprocess.CalledProcessError(1, ["dart", "pub", "get"])
    with (
        patch("static_analyzer.engine.adapters.dart_adapter.dart_binary", return_value="/sdk/dart"),
        patch("static_analyzer.engine.adapters.dart_adapter.subprocess.run", side_effect=error),
        pytest.raises(RuntimeError, match="Dependency resolution failed") as raised,
    ):
        DartAdapter().prepare_project(tmp_path)
    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    "expression,targets",
    [
        ("top();", ["top"]),
        ("obj.method(nested());", ["method", "nested"]),
        ("obj?.method();", ["method"]),
        ("obj..start()..stop();", ["start", "stop"]),
        ("Box<String>();", ["Box"]),
        ("new pkg.Box.named();", ["named"]),
        ("const pkg.Box.named();", ["named"]),
        ("const Box(value: item);", ["Box"]),
        ("new Box(item);", ["Box"]),
        ("const pkg.Box.named(value: item);", ["named"]),
        ("new pkg.Box<String>.named(value: item);", ["named"]),
        ("const Box<String>(item);", ["Box"]),
        ("const Box(child: Inner());", ["Box", "Inner"]),
        ("const Box(child: const Inner(value: item));", ["Box", "Inner"]),
        ("super.run();", ["run"]),
        ("obj.field; 'fake()'; // alsoFake()", []),
    ],
)
def test_dart_call_targets(tmp_path: Path, expression: str, targets: list[str]):
    source = "void main() {\n  " + expression + "\n}\n"
    path = tmp_path / "sample.dart"
    path.write_text(source, encoding="utf-8")
    actual = {(site.line, site.column) for site in SourceInspector().find_call_sites(path)}
    assert actual == {(2, expression.index(target) + 3) for target in targets}


def test_explicit_constructor_position_uses_utf16(tmp_path: Path):
    path = tmp_path / "sample.dart"
    path.write_text("void main() { '\U0001f600'; const Box(value: item); }", encoding="utf-8")
    sites = SourceInspector().find_call_sites(path)
    assert {(site.line, site.column) for site in sites} == {(1, 27)}


def test_super_call_does_not_dispatch_to_overrides(tmp_path: Path):
    path = tmp_path / "sample.dart"
    path.write_text("class Child extends Base {\n  void run() { super.run(); this.run(); }\n}\n")
    inspector = SourceInspector()
    assert inspector.names_base_member(CallSite(str(path), 2, 22))
    assert not inspector.names_base_member(CallSite(str(path), 2, 33))


@pytest.mark.parametrize(
    "expression,reads,writes",
    [
        ("obj.value;", [5], []),
        ("obj?.value;", [6], []),
        ("obj.value = other.value;", [19], [5]),
        ("obj..value = other.value;", [20], [6]),
        ("obj.value += 1;", [], [5]),
        ("obj.value++;", [], [5]),
        ("++obj.value;", [], [7]),
        ("obj.value.child = 1;", [5], []),
    ],
)
def test_dart_member_access_positions(tmp_path: Path, expression: str, reads: list[int], writes: list[int]):
    path = tmp_path / "sample.dart"
    path.write_text(f"void f() {{\n{expression}\n}}")
    actual_reads, actual_writes = SourceInspector().find_member_sites(path, {"value"})
    assert [(site.line, site.column) for site in actual_reads] == [(2, col) for col in reads]
    assert [(site.line, site.column) for site in actual_writes] == [(2, col) for col in writes]


def test_accessors_remain_distinct_and_resolve_like_incremental_edges(tmp_path: Path):
    path = tmp_path / "sample.dart"
    path.write_text(
        "class Box {\n int get value => 1;\n set value(int x) {}\n int field = 0;\n}\n"
        "void f(Box b) {\n b.value;\n b.value = 1;\n b.field;\n}\n"
    )
    symbols = []
    for name, kind, line, col, end in [
        ("Box", NodeType.CLASS, 0, 6, 4),
        ("value", NodeType.PROPERTY, 1, 9, 1),
        ("value", NodeType.PROPERTY, 2, 5, 2),
        ("field", NodeType.FIELD, 3, 5, 3),
        ("f", NodeType.FUNCTION, 5, 5, 9),
    ]:
        symbols.append(
            {
                "name": name,
                "kind": kind,
                "range": {"start": {"line": line, "character": 0}, "end": {"line": end, "character": 21}},
                "selectionRange": {"start": {"line": line, "character": col}},
            }
        )
    symbols[0]["children"] = symbols[1:4]
    lsp = MagicMock()
    lsp.document_symbol.return_value = [symbols[0], symbols[4]]
    answers = {(6, 3): (1, 9), (7, 3): (2, 5), (8, 3): (3, 5)}
    lsp.send_definition_batch.side_effect = lambda queries: [
        (
            [
                {
                    "uri": path.as_uri(),
                    "range": {"start": {"line": answers[(line, col)][0], "character": answers[(line, col)][1]}},
                }
            ]
            if (line, col) in answers
            else []
        )
        for _, line, col in queries
    ]
    lsp.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]
    adapter = DartAdapter()
    builder = CallGraphBuilder(lsp, adapter, tmp_path, tmp_path)
    result = builder.build([path], skip_hierarchy=True)
    assert {(edge.source, edge.destination) for edge in result.cfg.edges} == {
        ("sample.f", "sample.Box"),
        ("sample.f", "sample.Box.value"),
        ("sample.f", "sample.Box.value(set)"),
    }
    graph = convert_to_codeboarding_format(builder.symbol_table, result, adapter)["call_graph"]
    for line, col, site_kind, expected in [
        (1, 9, MEMBER_READ, "sample.Box.value"),
        (2, 5, MEMBER_WRITE, "sample.Box.value(set)"),
    ]:
        targets = targets_for(
            GraphIndex(graph), SourceInspector(), str(path), line, col, site_kind, adapter, CallSite(str(path), 7, 4)
        )
        assert {node.fully_qualified_name for node in targets.nodes} == {expected, "sample.Box"}
    assert builder.symbol_table.symbols["sample.Box.field"].kind == NodeType.FIELD
