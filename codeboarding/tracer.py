import argparse
import os
import shutil
import sys
from pathlib import Path

# Try to import from utils if available, otherwise fallback
try:
    # Add root to sys.path to allow importing utils when run as a module
    root_path = Path(__file__).resolve().parent.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    from utils import get_project_root
except ImportError:

    def get_project_root() -> Path:
        return Path(os.getcwd())


DEFAULT_TOOLS = ["grep", "ls", "git", "find", "cat", "rm", "mkdir", "cp", "mv", "pytest"]

SHIM_TEMPLATE = """#!/bin/bash

# Real Binary Resolution
REAL_BIN="{real_bin}"
TOOL="{tool}"
LOG_FILE="{log_file}"
SHIM_PID=$$
SHIM_PPID=$PPID

# Capture and log in one python call for efficiency and JSON safety
python3 -c "import time, sys, json, os; ts = int(time.time()*1000); tool = sys.argv[1]; log_file = sys.argv[2]; shim_pid = int(sys.argv[3]); shim_ppid = int(sys.argv[4]); cmd_args = sys.argv[5:]; data = {{'ts': ts, 'tool': tool, 'args': cmd_args, 'cwd': os.getcwd(), 'pid': shim_pid, 'ppid': shim_ppid}}; os.makedirs(os.path.dirname(log_file), exist_ok=True); f = open(log_file, 'a'); f.write(json.dumps(data) + '\\n'); f.flush(); os.fsync(f.fileno()); f.close()" "$TOOL" "$LOG_FILE" "$SHIM_PID" "$SHIM_PPID" "$@"

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
    parser = argparse.ArgumentParser(description="CodeBoarding CLI Tracer")
    parser.add_argument("--tools", nargs="+", default=DEFAULT_TOOLS, help="List of tools to shim")
    args = parser.parse_args()

    root = get_project_root()
    cb_dir = root / ".codeboarding"
    shim_dir = cb_dir / "shims"
    trace_dir = cb_dir / "traces"
    log_file = trace_dir / "current.jsonl"

    shim_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    for tool in args.tools:
        real_bin = find_real_binary(tool, shim_dir)
        if not real_bin:
            continue

        shim_path = shim_dir / tool
        content = SHIM_TEMPLATE.format(real_bin=real_bin, tool=tool, log_file=str(log_file.absolute()))

        with open(shim_path, "w") as f:
            f.write(content)

        shim_path.chmod(0o755)

    # Print activation command
    print(f'export PATH="{shim_dir}:$PATH"')


if __name__ == "__main__":
    main()
