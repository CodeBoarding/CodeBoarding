import subprocess
from pathlib import Path

from static_analyzer import dotnet_sdk


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_find_global_json_uses_nearest_ancestor(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "global.json").write_text('{"sdk": {"version": "10.0.100"}}')
    nested = tmp_path / "src" / "App"
    nested.mkdir(parents=True)
    (tmp_path / "src" / "global.json").write_text('{"sdk": {"version": "10.0.301"}}')

    assert dotnet_sdk.find_global_json(nested, tmp_path) == tmp_path / "src" / "global.json"
    assert dotnet_sdk.read_global_sdk_version(tmp_path / "src" / "global.json") == "10.0.301"


def test_find_global_json_ignores_parent_outside_repository(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "src" / "App"
    nested.mkdir(parents=True)
    (tmp_path / "global.json").write_text('{"sdk": {"version": "10.0.100"}}')

    assert dotnet_sdk.find_global_json(nested, repo) is None


def test_find_global_json_uses_snapshot_worktree_boundary(tmp_path):
    snapshot = tmp_path / ".codeboarding" / "snapshot-worktree"
    project = snapshot / "src" / "App"
    project.mkdir(parents=True)
    (tmp_path / "global.json").write_text('{"sdk": {"version": "10.0.100"}}')

    assert dotnet_sdk.find_global_json(project, snapshot) is None


def test_resolve_uses_satisfying_system_dotnet(tmp_path, monkeypatch):
    calls = []
    system_dotnet = tmp_path / "system" / "dotnet"
    system_dotnet.parent.mkdir()
    system_dotnet.write_text("")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "--version":
            return _proc(stdout="10.0.301\n")
        if cmd[1] == "--list-sdks":
            return _proc(stdout="10.0.301 [/usr/share/dotnet/sdk]\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(dotnet_sdk, "user_data_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(dotnet_sdk.shutil, "which", lambda name: str(system_dotnet) if name == "dotnet" else None)
    monkeypatch.setattr(dotnet_sdk.subprocess, "run", fake_run)
    monkeypatch.setattr(
        dotnet_sdk, "_run_install_script", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError)
    )

    resolution = dotnet_sdk.resolve_dotnet_sdk(tmp_path)

    assert resolution.dotnet_path == str(system_dotnet)
    assert resolution.source == "system"
    assert [c[1] for c in calls] == ["--version", "--list-sdks"]


def test_resolve_installs_global_json_sdk_when_system_is_unsatisfied(tmp_path, monkeypatch):
    (tmp_path / "global.json").write_text('{"sdk": {"version": "10.0.301", "allowPrerelease": false}}')
    home = tmp_path / "home"
    installed = []

    def fake_install(args, install_dir):
        installed.append(args)
        install_dir.mkdir(parents=True, exist_ok=True)
        dotnet = install_dir / "dotnet"
        dotnet.write_text("")
        dotnet.chmod(0o755)

    def fake_run(cmd, **kwargs):
        if cmd[1] == "--version":
            return _proc(stdout="10.0.301\n") if Path(cmd[0]).exists() else _proc(returncode=1, stderr="missing")
        if cmd[1] == "--list-sdks":
            return _proc(stdout="10.0.301 [/private/sdk]\n") if Path(cmd[0]).exists() else _proc(returncode=1)
        raise AssertionError(cmd)

    monkeypatch.setattr(dotnet_sdk, "user_data_dir", lambda: home)
    monkeypatch.setattr(dotnet_sdk.shutil, "which", lambda _name: None)
    monkeypatch.setattr(dotnet_sdk.subprocess, "run", fake_run)
    monkeypatch.setattr(dotnet_sdk, "_run_install_script", fake_install)

    resolution = dotnet_sdk.resolve_dotnet_sdk(tmp_path)

    assert installed == [["--jsonfile", str(tmp_path / "global.json")]]
    assert resolution.dotnet_path == str(home / "dotnet" / "dotnet")
    assert resolution.env["DOTNET_ROOT"] == str(home / "dotnet")
    assert resolution.installed is True


def test_resolve_installs_tool_sdk_when_no_dotnet_exists(tmp_path, monkeypatch):
    home = tmp_path / "home"
    installed = []

    def fake_install(args, install_dir):
        installed.append(args)
        install_dir.mkdir(parents=True, exist_ok=True)
        dotnet = install_dir / "dotnet"
        dotnet.write_text("")
        dotnet.chmod(0o755)

    def fake_run(cmd, **kwargs):
        if cmd[1] == "--version":
            return _proc(stdout="10.0.100\n") if Path(cmd[0]).exists() else _proc(returncode=1)
        if cmd[1] == "--list-sdks":
            return _proc(stdout="10.0.100 [/private/sdk]\n") if Path(cmd[0]).exists() else _proc(returncode=1)
        raise AssertionError(cmd)

    monkeypatch.setattr(dotnet_sdk, "user_data_dir", lambda: home)
    monkeypatch.setattr(dotnet_sdk.shutil, "which", lambda _name: None)
    monkeypatch.setattr(dotnet_sdk.subprocess, "run", fake_run)
    monkeypatch.setattr(dotnet_sdk, "_run_install_script", fake_install)

    resolution = dotnet_sdk.resolve_dotnet_sdk(tmp_path)

    assert installed == [["--channel", "10.0"]]
    assert resolution.source == "private"
    assert resolution.installed is True
