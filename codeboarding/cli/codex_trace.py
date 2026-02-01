import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# Try to import from utils if available, otherwise fallback
try:
    # Add project root to sys.path to allow importing project modules
    # codeboarding/cli/codex_trace.py -> parent.parent.parent is project root
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from utils import get_project_root
except ImportError:

    def get_project_root() -> Path:
        """Fallback if utils.get_project_root is not available."""
        return Path(os.getcwd())


_SENSITIVE_PATTERN = re.compile(
    r"(?i)(--(?:api[-_]?key|token|secret|password)|" r"(?:api[-_]?key|token|secret|password)=)"
)

_CODEX_TOP_LEVEL_COMMANDS = {
    "exec",
    "review",
    "login",
    "logout",
    "mcp",
    "mcp-server",
    "app-server",
    "completion",
    "sandbox",
    "apply",
    "resume",
    "fork",
    "cloud",
    "features",
    "help",
}


def _ensure_json_flag(args: list[str]) -> list[str]:
    for flag in ("--json", "--experimental-json"):
        if flag in args:
            return args
    if args and args[0] == "exec":
        return [args[0], "--json", *args[1:]]
    return ["--json", *args]


def _inject_exec_if_missing(args: list[str], allow_non_exec: bool) -> list[str]:
    first_non_flag = None
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-"):
            continue
        first_non_flag = arg
        break

    if first_non_flag is None:
        return ["exec", *args]

    if first_non_flag in _CODEX_TOP_LEVEL_COMMANDS and first_non_flag != "exec":
        if allow_non_exec:
            return args
        raise ValueError(
            "codex_trace only supports 'codex exec' JSONL output. "
            "Use '--allow-non-exec' to bypass or run with 'exec'."
        )

    if first_non_flag == "exec":
        return args

    return ["exec", *args]


def _parse_json_line(line: str) -> dict | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _extract_tracked_item(event: dict) -> dict | None:
    event_type = event.get("type")
    if event_type not in {"item.started", "item.updated", "item.completed"}:
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") not in {"command_execution", "file_change", "mcp_tool_call"}:
        return None
    return item


def _redact_command(command: str) -> str:
    if not command:
        return command
    parts = command.split()
    redacted: list[str] = []
    skip_next = False
    for part in parts:
        if skip_next:
            redacted.append("[REDACTED]")
            skip_next = False
            continue
        if _SENSITIVE_PATTERN.search(part):
            if part.startswith("--") and "=" not in part:
                redacted.append(part)
                skip_next = True
                continue
            if "=" in part:
                key, _, _ = part.partition("=")
                redacted.append(f"{key}=[REDACTED]")
                continue
        redacted.append(part)
    return " ".join(redacted)


def _write_trace(log_file: Path, payload: dict) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(json.dumps(payload) + "\n")


def _stream_pipe(pipe, write_fn):
    for line in iter(pipe.readline, ""):
        write_fn(line)
    pipe.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeBoarding Codex tracer - capture Codex JSONL command execution events"
    )
    parser.add_argument(
        "--codex-path",
        default="codex",
        help="Path to the Codex CLI binary (default: codex)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to write JSONL trace output (default: .codeboarding/traces/codex_commands.jsonl)",
    )
    parser.add_argument(
        "--include-output",
        action="store_true",
        help="Include aggregated command output in trace logs (disabled by default)",
    )
    parser.add_argument(
        "--include-mcp-arguments",
        action="store_true",
        help="Include MCP tool arguments in trace logs (disabled by default)",
    )
    parser.add_argument(
        "--include-mcp-result",
        action="store_true",
        help="Include MCP tool result payload in trace logs (disabled by default)",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable redaction of sensitive arguments in command strings",
    )
    parser.add_argument(
        "--allow-non-exec",
        action="store_true",
        help="Allow non-exec Codex subcommands (disables JSONL enforcement)",
    )
    parser.add_argument(
        "codex_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to Codex (prefix with -- to avoid parsing)",
    )

    args = parser.parse_args()
    codex_args = args.codex_args
    if codex_args and codex_args[0] == "--":
        codex_args = codex_args[1:]

    codex_args = _inject_exec_if_missing(codex_args, args.allow_non_exec)
    if not args.allow_non_exec:
        codex_args = _ensure_json_flag(codex_args)
    cmd = [args.codex_path, *codex_args]

    root = get_project_root()
    default_log = root / ".codeboarding" / "traces" / "codex_commands.jsonl"
    log_file = Path(args.log_file) if args.log_file else default_log

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    if not proc.stdout or not proc.stderr:
        raise RuntimeError("Failed to attach to Codex stdout/stderr streams.")

    stderr_thread = threading.Thread(
        target=_stream_pipe,
        args=(proc.stderr, lambda line: (sys.stderr.write(line), sys.stderr.flush())),
        daemon=True,
    )
    stderr_thread.start()

    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()

        event = _parse_json_line(line)
        if not event:
            continue
        item = _extract_tracked_item(event)
        if not item:
            continue

        payload = {
            "ts": int(time.time() * 1000),
            "event": event.get("type"),
            "item_id": item.get("id"),
            "source": "codex_jsonl",
            "item_type": item.get("type"),
        }

        item_type = item.get("type")
        if item_type == "command_execution":
            command = item.get("command", "")
            if not args.no_redact:
                command = _redact_command(command)
            payload.update(
                {
                    "command": command,
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                }
            )
            if args.include_output:
                payload["aggregated_output"] = item.get("aggregated_output", "")
        elif item_type == "file_change":
            payload.update(
                {
                    "status": item.get("status"),
                    "changes": item.get("changes", []),
                }
            )
        elif item_type == "mcp_tool_call":
            payload.update(
                {
                    "status": item.get("status"),
                    "server": item.get("server"),
                    "tool": item.get("tool"),
                }
            )
            if args.include_mcp_arguments:
                payload["arguments"] = item.get("arguments")
            if args.include_mcp_result:
                payload["result"] = item.get("result")
                payload["error"] = item.get("error")

        _write_trace(log_file, payload)

    proc.stdout.close()
    return_code = proc.wait()
    stderr_thread.join(timeout=1.0)
    sys.exit(return_code)


if __name__ == "__main__":
    main()
