from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from static_analyzer.config import Language
from static_analyzer.engine.adapters.dart_adapter import DartAdapter
from static_analyzer.engine.models import CallSite
from static_analyzer.engine.source_inspector import SourceInspector
from infra.tool_registry.manifest import has_required_tools, resolve_config_from_path
from infra.tool_registry.installers import archive_stamp, ARCHIVE_VERSION_STAMP
from infra.tool_registry.paths import exe_suffix
from infra.tool_registry.registry import TOOL_REGISTRY
from infra.vscode_constants import VSCODE_CONFIG


@pytest.fixture
def managed_dart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(VSCODE_CONFIG["lsp_servers"][Language.DART], "command", ["dart", "language-server"])
    binary = tmp_path / f"servers/bin/flutter/bin/cache/dart-sdk/bin/dart{exe_suffix()}"
    binary.parent.mkdir(parents=True)
    binary.touch(mode=0o755)
    (binary.parents[2] / "flutter_tools.snapshot").touch()
    monkeypatch.setattr("static_analyzer.engine.adapters.dart_adapter.get_servers_dir", lambda: tmp_path / "servers")
    return binary


@pytest.mark.parametrize("system,suffix", [("Linux", ""), ("Darwin", ""), ("Windows", ".exe")])
def test_managed_sdk_without_system_sdk(tmp_path: Path, managed_dart: Path, system: str, suffix: str):
    binary = managed_dart.rename(managed_dart.with_name(f"dart{suffix}"))
    with (
        patch("infra.tool_registry.paths.platform.system", return_value=system),
        patch("shutil.which", side_effect=AssertionError("SDK discovery must not use PATH")),
    ):
        assert DartAdapter().get_lsp_command(tmp_path) == [str(binary), "language-server"]
        binary.unlink()
        with pytest.raises(RuntimeError, match="Managed Dart SDK not found"):
            DartAdapter().get_lsp_command(tmp_path)


def test_setup_requires_managed_sdk(tmp_path: Path, managed_dart: Path):
    dart = next(dep for dep in TOOL_REGISTRY if dep.key == Language.DART)
    servers = tmp_path / "servers"
    with patch("shutil.which", return_value="/unrelated/sdk/dart"):
        assert not has_required_tools(servers, [dart])
        assert dart.source is not None
        (servers / "bin/flutter" / ARCHIVE_VERSION_STAMP).write_text(archive_stamp(dart.source))
        assert has_required_tools(servers, [dart])
        managed_dart.unlink()
        assert not has_required_tools(servers, [dart])
        assert resolve_config_from_path()["lsp_servers"][Language.DART]["command"] != [
            "/unrelated/sdk/dart",
            "language-server",
        ]


@pytest.mark.parametrize("flutter", [False, True])
def test_restores_project_with_matching_sdk(tmp_path: Path, managed_dart: Path, flutter: bool):
    manifest = "name: sample\n"
    if flutter:
        manifest += "dependencies:\n  flutter:\n    sdk: flutter\n"
    (tmp_path / "pubspec.yaml").write_text(manifest)
    ignored = tmp_path / ".dart_tool"
    ignored.mkdir()
    (ignored / "pubspec.yaml").write_text(manifest)
    with (
        patch("shutil.which", side_effect=AssertionError("SDK discovery must not use PATH")),
        patch("static_analyzer.engine.adapters.dart_adapter.subprocess.run") as run,
    ):
        DartAdapter().prepare_project(tmp_path)
    command = [str(managed_dart)]
    if flutter:
        command.extend([str(managed_dart.parents[2] / "flutter_tools.snapshot"), "--suppress-analytics"])
    run.assert_called_once_with(
        [*command, "pub", "get"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


def test_flutter_project_requires_complete_sdk(tmp_path: Path, managed_dart: Path):
    (tmp_path / "pubspec.yaml").write_text("dev_dependencies:\n  flutter_test:\n    sdk: flutter\n")
    (managed_dart.parents[2] / "flutter_tools.snapshot").unlink()
    with pytest.raises(RuntimeError, match="Managed Flutter SDK is incomplete"):
        DartAdapter().prepare_project(tmp_path)


def test_restore_failure_warns_and_restores_other_packages(
    tmp_path: Path, managed_dart: Path, caplog: pytest.LogCaptureFixture
):
    for package in ("broken", "healthy"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "pubspec.yaml").write_text(f"name: {package}\n")
    error = subprocess.CalledProcessError(1, ["dart", "pub", "get"], stderr="Could not find package private_dep")
    restored: list[Path] = []

    def run(command: list[str], cwd: Path, **kwargs: object) -> None:
        if cwd.name == "broken":
            raise error
        restored.append(cwd)

    with patch("static_analyzer.engine.adapters.dart_adapter.subprocess.run", side_effect=run):
        DartAdapter().prepare_project(tmp_path)
    assert restored == [tmp_path / "healthy"]
    [record] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "broken" in record.getMessage() and "private_dep" in record.getMessage()
    assert record.exc_info and record.exc_info[1] is error


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
        ("const Box(visible: true);", ["Box"]),
        ("const pkg.Box.named(mode: Mode.active);", ["named"]),
        ("const Box<String>(value: null);", ["Box"]),
        ("new pkg.Box.named(value: nested());", ["named", "nested"]),
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


@pytest.mark.parametrize("reference", ["Box.new", "Box<int>.new", "pkg.Box<int>.new", "Box. /* factory */ new"])
def test_dart_constructor_tearoff_targets(tmp_path: Path, reference: str):
    source = f"void main() {{ register<Box>({reference}); }}"
    path = tmp_path / "sample.dart"
    path.write_text(source, encoding="utf-8")
    actual = {(site.line, site.column) for site in SourceInspector().find_method_group_sites(path)}
    assert (1, source.index("new") + 1) in actual


def test_super_call_does_not_dispatch_to_overrides(tmp_path: Path):
    path = tmp_path / "sample.dart"
    path.write_text("class Child extends Base {\n  void run() { super.run(); this.run(); }\n}\n")
    inspector = SourceInspector()
    assert inspector.names_base_member(CallSite(str(path), 2, 22))
    assert not inspector.names_base_member(CallSite(str(path), 2, 33))
