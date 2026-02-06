import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.java_config_scanner import JavaProjectConfig
from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.lsp_client.java_client import JavaClient
from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.programming_language import JavaConfig, ProgrammingLanguage


REPO_ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def _working_directory(path: Path):
    current = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(current)


def _load_lsp_config() -> dict:
    config_path = REPO_ROOT / "static_analysis_config.yml"
    if not config_path.exists():
        pytest.skip(f"Missing config at {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _resolve_command_path(command: str) -> Path | None:
    if command.startswith("./") or "/" in command or "\\" in command:
        candidate = (REPO_ROOT / command).resolve()
        return candidate if candidate.exists() else None

    resolved = shutil.which(command)
    return Path(resolved) if resolved else None


def _create_language(server_key: str, server_config: dict) -> ProgrammingLanguage:
    language_specific_config = None
    if server_key == "java" and server_config.get("jdtls_root"):
        language_specific_config = JavaConfig(jdtls_root=Path(server_config["jdtls_root"]))
    return ProgrammingLanguage(
        language=server_key,
        size=0,
        percentage=0.0,
        suffixes=server_config.get("file_extensions", []),
        server_commands=server_config.get("command"),
        lsp_server_key=server_key,
        language_specific_config=language_specific_config,
    )


def _create_client(server_key: str, language: ProgrammingLanguage) -> LSPClient:
    ignore_manager = RepoIgnoreManager(REPO_ROOT)
    if server_key in {"typescript", "javascript"}:
        return TypeScriptClient(language=language, project_path=REPO_ROOT, ignore_manager=ignore_manager)
    if server_key == "java":
        project_config = JavaProjectConfig(root=REPO_ROOT, build_system="none", is_multi_module=False)
        return JavaClient(
            project_path=REPO_ROOT,
            language=language,
            project_config=project_config,
            ignore_manager=ignore_manager,
        )
    return LSPClient(language=language, project_path=REPO_ROOT, ignore_manager=ignore_manager)


def _server_available(server_key: str, server_config: dict) -> tuple[bool, str]:
    command = server_config.get("command", [])
    if not command:
        return False, "missing command"

    if server_key == "java":
        if not shutil.which("java"):
            return False, "java not found in PATH"
        jdtls_root = server_config.get("jdtls_root")
        if not jdtls_root or not Path(jdtls_root).exists():
            return False, "jdtls_root not found"
        return True, ""

    binary = _resolve_command_path(command[0])
    if not binary:
        return False, f"binary not found: {command[0]}"
    return True, ""


def test_lsp_startup_times():
    if os.getenv("RUN_LSP_STARTUP_BENCH", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set RUN_LSP_STARTUP_BENCH=1 to run LSP startup timing test.")

    config = _load_lsp_config()
    lsp_servers = config.get("lsp_servers", {})
    if not lsp_servers:
        pytest.skip("No LSP servers configured.")

    results: dict[str, dict[str, float]] = {}
    skipped: dict[str, str] = {}

    with _working_directory(REPO_ROOT):
        for server_key, server_config in lsp_servers.items():
            available, reason = _server_available(server_key, server_config)
            if not available:
                skipped[server_key] = reason
                continue

            language = _create_language(server_key, server_config)

            # Spawn-only timing: start process without initialize handshake.
            spawn_client = _create_client(server_key, language)
            spawn_client._initialize = lambda: None  # type: ignore[method-assign]
            spawn_start = time.time()
            try:
                spawn_client.start()
            except Exception as exc:
                pytest.fail(f"Failed to spawn {server_key}: {exc}")
            finally:
                if spawn_client._process:
                    spawn_client._shutdown_flag.set()
                    spawn_client._process.terminate()
                    try:
                        spawn_client._process.wait(timeout=5)
                    except Exception:
                        spawn_client._process.kill()
                if spawn_client._reader_thread:
                    spawn_client._reader_thread.join(timeout=2)
            spawn_elapsed = time.time() - spawn_start

            # Ready timing: full start + initialize handshake.
            ready_client = _create_client(server_key, language)
            ready_start = time.time()
            try:
                ready_client.start()
            except Exception as exc:
                pytest.fail(f"Failed to start {server_key}: {exc}")
            finally:
                ready_client.close()
            ready_elapsed = time.time() - ready_start

            results[server_key] = {"spawn_only": spawn_elapsed, "ready": ready_elapsed}

    if not results:
        pytest.skip(f"No LSP servers were available. Skipped: {skipped}")

    for server_key, timings in results.items():
        print(f"{server_key}: spawn_only={timings['spawn_only']:.2f}s ready={timings['ready']:.2f}s")
