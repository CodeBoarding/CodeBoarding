from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

import install
from static_analyzer.engine.adapters.dart_adapter import DartAdapter
from static_analyzer.engine.models import CallSite
from static_analyzer.engine.source_inspector import SourceInspector
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


def test_super_call_does_not_dispatch_to_overrides(tmp_path: Path):
    path = tmp_path / "sample.dart"
    path.write_text("class Child extends Base {\n  void run() { super.run(); this.run(); }\n}\n")
    inspector = SourceInspector()
    assert inspector.names_base_member(CallSite(str(path), 2, 22))
    assert not inspector.names_base_member(CallSite(str(path), 2, 33))
