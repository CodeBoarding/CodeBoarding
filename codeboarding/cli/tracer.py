import argparse
import os
import shutil
import sys
from pathlib import Path

# Try to import from utils if available, otherwise fallback
try:
    # Add project root to sys.path to allow importing project modules
    # codeboarding/cli/tracer.py -> parent.parent.parent is project root
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from utils import get_project_root
except ImportError:

    def get_project_root() -> Path:
        """Fallback if utils.get_project_root is not available."""
        return Path(os.getcwd())


# Tool categories for safer defaults
READ_ONLY_TOOLS = ["grep", "ls", "git", "find", "cat", "pytest"]
DESTRUCTIVE_TOOLS = ["rm", "mkdir", "cp", "mv"]
DEFAULT_TOOLS = READ_ONLY_TOOLS

SHIM_TEMPLATE = """#!/bin/bash

# Real Binary Resolution
REAL_BIN="{real_bin}"
TOOL="{tool}"
LOG_FILE="{log_file}"
REPO_ROOT="{repo_root}"
SHIM_PID=$$
SHIM_PPID=$PPID

# Capture and log only if inside repo
# Uses python3 to log execution details to a JSONL file
python3 -c "import time, sys, json, os; cwd = os.getcwd(); root = sys.argv[5]; sys.exit(0) if not (cwd + os.sep).startswith(root + os.sep) else None; ts = int(time.time()*1000); tool = sys.argv[1]; log_file = sys.argv[2]; shim_pid = int(sys.argv[3]); shim_ppid = int(sys.argv[4]); cmd_args = sys.argv[6:]; data = {{'ts': ts, 'tool': tool, 'args': cmd_args, 'cwd': cwd, 'pid': shim_pid, 'ppid': shim_ppid}}; os.makedirs(os.path.dirname(log_file), exist_ok=True); f = open(log_file, 'a'); f.write(json.dumps(data) + '\\n'); f.flush(); os.fsync(f.fileno()); f.close()" "$TOOL" "$LOG_FILE" "$SHIM_PID" "$SHIM_PPID" "$REPO_ROOT" "$@"

# Execute Real Binary
exec "$REAL_BIN" "$@"
"""


def find_real_binary(tool: str, shim_dir: Path) -> str | None:
    """Find the absolute path of the real binary, ignoring the shim directory."""
    original_path = os.environ.get("PATH", "")
    # Remove shim_dir from PATH to find the real binary
    paths = original_path.split(os.pathsep)
    filtered_paths = [p for p in paths if Path(p).resolve() != shim_dir.resolve()]

    return shutil.which(tool, path=os.pathsep.join(filtered_paths))


def main() -> None:
    """Main entry point for the CodeBoarding CLI Tracer."""
    parser = argparse.ArgumentParser(
        description="CodeBoarding CLI Tracer - Shim CLI tools to trace their execution within a repository"
    )
    parser.add_argument(
        "--tools",
        nargs="+",
        help=f"Specific list of tools to shim (default: {', '.join(READ_ONLY_TOOLS)})",
    )
    parser.add_argument(
        "--include-destructive",
        action="store_true",
        help=f"Also shim destructive tools ({', '.join(DESTRUCTIVE_TOOLS)})",
    )
    args = parser.parse_args()

    # Determine which tools to shim
    if args.tools:
        tools_to_shim = args.tools
    else:
        tools_to_shim = READ_ONLY_TOOLS.copy()
        if args.include_destructive:
            tools_to_shim.extend(DESTRUCTIVE_TOOLS)

    root = get_project_root()
    cb_dir = root / ".codeboarding"
    shim_dir = cb_dir / "shims"
    trace_dir = cb_dir / "traces"
    log_file = trace_dir / "current.jsonl"

    shim_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    for tool in tools_to_shim:
        real_bin = find_real_binary(tool, shim_dir)
        if not real_bin:
            continue

        shim_path = shim_dir / tool
        content = SHIM_TEMPLATE.format(
            real_bin=real_bin,
            tool=tool,
            log_file=str(log_file.absolute()),
            repo_root=str(root.absolute()),
        )

        with open(shim_path, "w") as f:
            f.write(content)

        shim_path.chmod(0o755)

    # Print activation command for the user
    print(f'export PATH="{shim_dir}:$PATH"')


if __name__ == "__main__":
    main()
