"""Private .NET SDK resolution for C# static analysis.

The adapter needs two things that are easy to conflate:

* the repository's SDK, as selected by ``global.json``;
* a .NET 10 SDK/runtime capable of installing and running ``csharp-ls``.

This module lets the .NET CLI make SDK-selection decisions, and only falls
back to a private install under ``~/.codeboarding/dotnet`` when the current
host cannot satisfy them.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tool_registry import exe_suffix, user_data_dir

logger = logging.getLogger(__name__)

TOOL_SDK_MAJOR = 10
TOOL_SDK_CHANNEL = "10.0"
DOTNET_INSTALL_TIMEOUT = 1800
DOTNET_PROBE_TIMEOUT = 30

_INSTALL_SCRIPT_URL = "https://dot.net/v1/dotnet-install.sh"
_INSTALL_SCRIPT_PS1_URL = "https://dot.net/v1/dotnet-install.ps1"


class DotnetSdkError(RuntimeError):
    """Raised when CodeBoarding cannot provide a usable .NET SDK."""


@dataclass(frozen=True)
class DotnetSdkResolution:
    dotnet_path: str
    env: dict[str, str]
    source: str
    global_json: Path | None = None
    requested_version: str | None = None
    installed: bool = False


@dataclass(frozen=True)
class _Probe:
    ok: bool
    stdout: str = ""
    stderr: str = ""


def dotnet_install_dir() -> Path:
    return user_data_dir() / "dotnet"


def private_dotnet_path(install_dir: Path | None = None) -> Path:
    return (install_dir or dotnet_install_dir()) / f"dotnet{exe_suffix()}"


def find_global_json(project_root: Path) -> Path | None:
    """Return the nearest ancestor ``global.json`` that .NET would consider."""
    current = project_root.resolve()
    for directory in (current, *current.parents):
        candidate = directory / "global.json"
        if candidate.is_file():
            return candidate
    return None


def read_global_sdk_version(global_json: Path) -> str | None:
    try:
        payload = json.loads(global_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("sdk", {}).get("version")
    return version if isinstance(version, str) and version else None


def resolve_dotnet_sdk(project_root: Path) -> DotnetSdkResolution:
    """Resolve or install the .NET SDK set needed for a C# project."""
    project_root = project_root.resolve()
    global_json = find_global_json(project_root)
    requested_version = read_global_sdk_version(global_json) if global_json else None

    install_dir = dotnet_install_dir()
    private_dotnet = private_dotnet_path(install_dir)
    private_env = _private_dotnet_env(install_dir)

    private_probe = _probe_dotnet(private_dotnet, project_root, private_env) if private_dotnet.exists() else None
    if private_probe and private_probe.ok and _has_sdk_major(private_dotnet, project_root, private_env, TOOL_SDK_MAJOR):
        return DotnetSdkResolution(
            dotnet_path=str(private_dotnet),
            env=private_env,
            source="private",
            global_json=global_json,
            requested_version=requested_version,
        )

    system_dotnet = shutil.which("dotnet")
    if system_dotnet:
        system_env = system_dotnet_env(Path(system_dotnet))
        system_probe = _probe_dotnet(Path(system_dotnet), project_root, system_env)
        if system_probe.ok and _has_sdk_major(Path(system_dotnet), project_root, system_env, TOOL_SDK_MAJOR):
            return DotnetSdkResolution(
                dotnet_path=system_dotnet,
                env=system_env,
                source="system",
                global_json=global_json,
                requested_version=requested_version,
            )

    installed = False
    if global_json and not (private_probe and private_probe.ok):
        if requested_version is None:
            raise DotnetSdkError(
                f"{global_json} exists but does not contain sdk.version. "
                "Install a compatible .NET SDK or fix global.json before analyzing C#."
            )
        _install_from_global_json(global_json, install_dir)
        installed = True

    if not _has_sdk_major(private_dotnet, project_root, private_env, TOOL_SDK_MAJOR):
        _install_channel(TOOL_SDK_CHANNEL, install_dir)
        installed = True

    final_probe = _probe_dotnet(private_dotnet, project_root, private_env)
    if not final_probe.ok:
        detail = (final_probe.stderr or final_probe.stdout).strip()
        pinned = f" required by {global_json}" if global_json else ""
        raise DotnetSdkError(
            f"Unable to resolve the .NET SDK{pinned}. "
            f"Install the SDK manually or retry with network access. {detail[-500:]}"
        )
    if not _has_sdk_major(private_dotnet, project_root, private_env, TOOL_SDK_MAJOR):
        raise DotnetSdkError(
            f"Unable to install a .NET {TOOL_SDK_MAJOR} SDK for csharp-ls under {install_dir}. "
            "Install .NET 10.0+ manually and retry."
        )

    return DotnetSdkResolution(
        dotnet_path=str(private_dotnet),
        env=private_env,
        source="private",
        global_json=global_json,
        requested_version=requested_version,
        installed=installed,
    )


def system_dotnet_env(dotnet_path: Path | None = None) -> dict[str, str]:
    """Return DOTNET_ROOT for known system layouts when the user has not set one."""
    if os.environ.get("DOTNET_ROOT"):
        return {}
    if dotnet_path is None:
        found = shutil.which("dotnet")
        if not found:
            return {}
        dotnet = Path(found)
    else:
        dotnet = dotnet_path
    if not str(dotnet):
        return {}
    try:
        resolved = dotnet.resolve()
    except OSError:
        return {}

    candidates = [
        resolved.parent,
        resolved.parent.parent / "libexec",
    ]
    for candidate in candidates:
        if not (candidate / f"dotnet{exe_suffix()}").exists():
            continue
        if candidate.name == "libexec" or (candidate / "sdk").is_dir() or (candidate / "shared").is_dir():
            return {"DOTNET_ROOT": str(candidate)}
    return {}


def _private_dotnet_env(install_dir: Path) -> dict[str, str]:
    path = os.environ.get("PATH", "")
    return {
        "DOTNET_ROOT": str(install_dir),
        "PATH": str(install_dir) + (os.pathsep + path if path else ""),
    }


def _merged_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(extra)
    return env


def _probe_dotnet(dotnet_path: Path, cwd: Path, env: dict[str, str]) -> _Probe:
    try:
        result = subprocess.run(
            [str(dotnet_path), "--version"],
            cwd=str(cwd),
            env=_merged_env(env),
            capture_output=True,
            text=True,
            timeout=DOTNET_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _Probe(False, stderr=str(exc))
    return _Probe(result.returncode == 0, result.stdout, result.stderr)


def _has_sdk_major(dotnet_path: Path, cwd: Path, env: dict[str, str], major: int) -> bool:
    if not dotnet_path.exists() and dotnet_path.is_absolute():
        return False
    try:
        result = subprocess.run(
            [str(dotnet_path), "--list-sdks"],
            cwd=str(cwd),
            env=_merged_env(env),
            capture_output=True,
            text=True,
            timeout=DOTNET_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        version = line.split(maxsplit=1)[0]
        try:
            if int(version.split(".", 1)[0]) >= major:
                return True
        except (IndexError, ValueError):
            continue
    return False


def _install_from_global_json(global_json: Path, install_dir: Path) -> None:
    logger.info("Installing .NET SDK from %s into %s", global_json, install_dir)
    _run_install_script(["--jsonfile", str(global_json)], install_dir)


def _install_channel(channel: str, install_dir: Path) -> None:
    logger.info("Installing .NET SDK channel %s into %s", channel, install_dir)
    _run_install_script(["--channel", channel], install_dir)


def _run_install_script(args: list[str], install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    script = _download_install_script()
    if platform.system() == "Windows":
        powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        ps_args = _to_powershell_install_args(args)
        cmd = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *ps_args,
            "-InstallDir",
            str(install_dir),
            "-NoPath",
        ]
    else:
        cmd = [
            shutil.which("bash") or "sh",
            str(script),
            *args,
            "--install-dir",
            str(install_dir),
            "--no-path",
        ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=DOTNET_INSTALL_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DotnetSdkError(f"dotnet-install failed (exit {result.returncode}): {detail[-1000:]}")


def _download_install_script() -> Path:
    scripts_dir = user_data_dir() / "dotnet-install"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        target = scripts_dir / "dotnet-install.ps1"
        url = _INSTALL_SCRIPT_PS1_URL
    else:
        target = scripts_dir / "dotnet-install.sh"
        url = _INSTALL_SCRIPT_URL
    if target.exists():
        return target

    tmp = target.with_name(target.name + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            tmp.write_bytes(response.read())
    except (OSError, urllib.error.URLError) as exc:
        tmp.unlink(missing_ok=True)
        raise DotnetSdkError(f"Could not download {url}: {exc}") from exc
    os.replace(tmp, target)
    if platform.system() != "Windows":
        target.chmod(0o755)
    return target


def _to_powershell_install_args(args: list[str]) -> list[str]:
    converted: list[str] = []
    mapping = {
        "--jsonfile": "-JsonFile",
        "--channel": "-Channel",
    }
    for arg in args:
        converted.append(mapping.get(arg, arg))
    return converted
