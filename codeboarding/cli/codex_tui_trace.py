import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# Try to import from utils if available, otherwise fallback
try:
    # Add project root to sys.path to allow importing project modules
    # codeboarding/cli/codex_tui_trace.py -> parent.parent.parent is project root
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

_SUPPORTED_EVENT_TYPES = {
    "exec_command_begin",
    "exec_command_end",
    "patch_apply_begin",
    "patch_apply_end",
    "mcp_tool_call_begin",
    "mcp_tool_call_end",
}

_PATH_OPTION_ARGS = {
    "rg": {"-g", "--glob", "-f", "--file", "-e", "--regexp", "-r", "--replace", "-A", "-B", "-C"},
    "grep": {"-e", "-f", "-m", "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir"},
    "ag": {"-G", "-g", "--ignore", "--ignore-dir"},
    "ack": {"-g", "-f", "--type", "--ignore-dir"},
    "find": {"-name", "-path", "-type", "-maxdepth", "-mindepth", "-regex", "-iname"},
}


def _get_codex_home() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home)
    return Path.home() / ".codex"


def _get_codex_log_dir() -> Path:
    return _get_codex_home() / "log"


def _redact_args(args: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in args:
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
    return redacted


def _summarize_changes(changes: dict) -> list[dict]:
    summarized: list[dict] = []
    for path, change in changes.items():
        if not isinstance(change, dict):
            continue
        kind = change.get("type")
        entry = {"path": str(path), "kind": kind}
        if kind == "update":
            move_path = change.get("move_path")
            if move_path:
                entry["move_path"] = str(move_path)
        summarized.append(entry)
    return summarized


def _split_pipeline(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token == "|":
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _strip_redirections(tokens: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>", "<", "2>", "2>>", "&>"}:
            skip_next = True
            continue
        cleaned.append(token)
    return cleaned


def _extract_paths_from_tokens(tokens: list[str], cwd: str) -> list[str]:
    if not tokens:
        return []
    cmd = Path(tokens[0]).name
    args = tokens[1:]
    paths: list[str] = []

    def add_path(value: str, allow_bare: bool = False) -> None:
        if not value or value == "-":
            return
        if value.startswith("-"):
            return
        if value in {".", ".."}:
            paths.append(str(Path(cwd) / value))
            return
        if "/" in value or value.endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml")):
            paths.append(str((Path(cwd) / value).resolve()) if not Path(value).is_absolute() else value)
            return
        if allow_bare:
            paths.append(str((Path(cwd) / value).resolve()))

    if cmd in {"cat", "sed", "head", "tail", "less", "more", "bat"}:
        for arg in args:
            if arg.startswith("-"):
                continue
            add_path(arg, allow_bare=True)
    elif cmd in {"rg", "grep", "ag", "ack"}:
        option_args = _PATH_OPTION_ARGS.get(cmd, set())
        filtered: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg in option_args:
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            filtered.append(arg)
        if filtered:
            # First non-option is usually the pattern; remaining are paths.
            for path_arg in filtered[1:]:
                add_path(path_arg, allow_bare=True)
    elif cmd == "ls":
        for arg in args:
            if arg.startswith("-"):
                continue
            add_path(arg, allow_bare=True)
    elif cmd == "find":
        for arg in args:
            if arg.startswith("-") or arg in {"(", ")", "!"}:
                break
            add_path(arg, allow_bare=True)
    elif cmd == "git":
        if "--" in args:
            idx = args.index("--")
            for arg in args[idx + 1 :]:
                add_path(arg, allow_bare=True)
        elif args:
            sub = args[0]
            if sub in {"add", "rm", "restore", "checkout", "diff", "status"}:
                for arg in args[1:]:
                    add_path(arg)
    elif cmd in {"python", "python3", "node"}:
        for arg in args:
            if arg.startswith("-"):
                continue
            if arg.endswith(".py") or arg.endswith(".js"):
                add_path(arg, allow_bare=True)
                break

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _extract_paths_from_command(command: list[str], cwd: str) -> list[str]:
    if not command:
        return []
    if len(command) >= 3 and command[1] in {"-lc", "-c"}:
        script = command[2]
        try:
            tokens = shlex.split(script)
        except ValueError:
            return []
        tokens = _strip_redirections(tokens)
        segments = _split_pipeline(tokens)
        paths: list[str] = []
        for segment in segments:
            paths.extend(_extract_paths_from_tokens(segment, cwd))
        return paths
    tokens = _strip_redirections(command)
    return _extract_paths_from_tokens(tokens, cwd)


def _extract_tracked_event(record: dict) -> dict | None:
    if record.get("kind") != "codex_event":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    msg = payload.get("msg")
    if not isinstance(msg, dict):
        return None
    msg_type = msg.get("type")
    if msg_type not in _SUPPORTED_EVENT_TYPES:
        return None
    return msg


def _write_trace(log_file: Path, payload: dict) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(json.dumps(payload) + "\n")


def _find_new_session_log(log_dir: Path, known: set[Path], timeout_sec: float = 10.0) -> Path | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            candidates = sorted(log_dir.glob("session-*.jsonl"), key=lambda p: p.stat().st_mtime)
        except FileNotFoundError:
            candidates = []
        for path in candidates:
            if path not in known:
                return path
        time.sleep(0.1)
    return None


def _follow_session_log(path: Path, proc: subprocess.Popen, on_line) -> None:
    with open(path, "r") as f:
        while True:
            line = f.readline()
            if line:
                on_line(line)
                continue
            if proc.poll() is not None:
                # Drain any remaining lines after process exit.
                remainder = f.read()
                if remainder:
                    for rem_line in remainder.splitlines(True):
                        on_line(rem_line)
                break
            time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeBoarding Codex TUI tracer - capture safe events from Codex interactive sessions"
    )
    parser.add_argument(
        "--codex-path",
        default="codex",
        help="Path to the Codex CLI binary (default: codex)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to write filtered JSONL trace output (default: .codeboarding/traces/codex_tui_filtered.jsonl)",
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
        "--no-trace",
        action="store_true",
        help="Disable tracing and run Codex normally",
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

    # Allow wrapper flags after the "--" separator by extracting them from codex_args.
    wrapper_flags = {
        "--include-mcp-arguments": "include_mcp_arguments",
        "--include-mcp-result": "include_mcp_result",
        "--no-redact": "no_redact",
        "--no-trace": "no_trace",
    }
    normalized_args: list[str] = []
    for arg in codex_args:
        if arg in wrapper_flags:
            setattr(args, wrapper_flags[arg], True)
            continue
        normalized_args.append(arg)
    codex_args = normalized_args

    cmd = [args.codex_path, *codex_args]

    root = get_project_root()
    default_log = root / ".codeboarding" / "traces" / "codex_tui_filtered.jsonl"
    log_file = Path(args.log_file) if args.log_file else default_log

    if args.no_trace or os.environ.get("CODEBOARDING_TRACE") in {"0", "false", "FALSE"}:
        os.execvp(args.codex_path, cmd)

    codex_log_dir = _get_codex_log_dir()
    known_logs = set(codex_log_dir.glob("session-*.jsonl")) if codex_log_dir.exists() else set()

    banner = (
        "CodeBoarding trace enabled (Codex TUI)\n"
        f"- Codex log: {codex_log_dir}/session-*.jsonl (default)\n"
        f"- Filtered log: {log_file}\n"
        "- Captures: command execs, patch apply, MCP tool calls (no user text)\n"
    )
    sys.stderr.write(banner)
    sys.stderr.flush()

    env = os.environ.copy()
    env["CODEX_TUI_RECORD_SESSION"] = "1"

    proc = subprocess.Popen(cmd, env=env)

    session_log = _find_new_session_log(codex_log_dir, known_logs)
    if not session_log:
        sys.stderr.write("Warning: could not find Codex session log; tracing disabled.\n")
        sys.stderr.flush()
        return_code = proc.wait()
        sys.exit(return_code)

    def handle_line(line: str) -> None:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        msg = _extract_tracked_event(record)
        if not msg:
            return

        msg_type = msg.get("type")
        payload: dict = {
            "ts": int(time.time() * 1000),
            "event": msg_type,
            "source": "codex_tui_session_log",
        }

        if msg_type in {"exec_command_begin", "exec_command_end"}:
            command = msg.get("command", [])
            if isinstance(command, list):
                cmd_args = command
            else:
                cmd_args = [str(command)]
            if not args.no_redact:
                cmd_args = _redact_args([str(p) for p in cmd_args])
            paths = _extract_paths_from_command(cmd_args, str(msg.get("cwd", "")))
            payload.update(
                {
                    "call_id": msg.get("call_id"),
                    "turn_id": msg.get("turn_id"),
                    "command": shlex.join(cmd_args),
                    "cwd": str(msg.get("cwd", "")),
                    "source_kind": msg.get("source"),
                    "paths": paths,
                }
            )
            if msg_type == "exec_command_begin":
                payload["process_id"] = msg.get("process_id")
            else:
                payload["exit_code"] = msg.get("exit_code")
                payload["duration"] = msg.get("duration")
        elif msg_type in {"patch_apply_begin", "patch_apply_end"}:
            changes = msg.get("changes", {})
            summarized = _summarize_changes(changes if isinstance(changes, dict) else {})
            payload.update(
                {
                    "call_id": msg.get("call_id"),
                    "turn_id": msg.get("turn_id"),
                    "success": msg.get("success"),
                    "changes": summarized,
                }
            )
        elif msg_type in {"mcp_tool_call_begin", "mcp_tool_call_end"}:
            invocation = msg.get("invocation", {}) if isinstance(msg.get("invocation"), dict) else {}
            payload.update(
                {
                    "call_id": msg.get("call_id"),
                    "server": invocation.get("server"),
                    "tool": invocation.get("tool"),
                }
            )
            if args.include_mcp_arguments:
                payload["arguments"] = invocation.get("arguments")
            if msg_type == "mcp_tool_call_end":
                payload["duration"] = msg.get("duration")
                if args.include_mcp_result:
                    payload["result"] = msg.get("result")
        _write_trace(log_file, payload)

    _follow_session_log(session_log, proc, handle_line)
    return_code = proc.wait()
    sys.exit(return_code)


if __name__ == "__main__":
    main()
