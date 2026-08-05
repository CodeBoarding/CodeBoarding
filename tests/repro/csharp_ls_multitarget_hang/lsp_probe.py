#!/usr/bin/env python3
"""Minimal LSP driver for csharp-ls: prints the server's load narration and times the first documentSymbol.

Mimics CodeBoarding's LSPClient handshake, then sends textDocument/documentSymbol on the first
.cs file — the "request 2" whose timeout appears in production logs as
"Timeout waiting for LSP response to request 2". See docs/development/csharp-ls-multitarget-hang.md.

Usage: lsp_probe.py <project_root> [timeout_seconds]
The csharp-ls binary is resolved from $CSHARP_LS, then PATH, then the CodeBoarding servers dir.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:8.1f}s] {msg}", flush=True)


def resolve_csharp_ls() -> str:
    candidate = os.environ.get("CSHARP_LS") or shutil.which("csharp-ls")
    if candidate:
        return candidate
    pattern = str(Path.home() / ".codeboarding" / "servers" / "bin" / "*" / "pm-tools" / "csharp-ls" / "csharp-ls")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    raise SystemExit("csharp-ls not found: set CSHARP_LS, add it to PATH, or install via CodeBoarding setup")


def main() -> int:
    project_root = Path(sys.argv[1]).resolve()
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 1800

    first_file = sorted(project_root.rglob("*.cs"))[0]
    log(f"project={project_root} probe_file={first_file.relative_to(project_root)}")

    proc = subprocess.Popen(
        [resolve_csharp_ls()],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=open(project_root / "csharp-ls.stderr.log", "wb"),
        env=os.environ.copy(),
        cwd=str(project_root),
    )
    stdin = proc.stdin
    stdout = proc.stdout
    assert stdin is not None and stdout is not None

    lock = threading.Lock()
    responses: dict[int, dict] = {}
    next_id = [0]

    def send(method: str, params: dict, is_request: bool) -> int:
        msg: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        req_id = 0
        if is_request:
            next_id[0] += 1
            req_id = next_id[0]
            msg["id"] = req_id
        raw = json.dumps(msg).encode()
        with lock:
            stdin.write(b"Content-Length: %d\r\n\r\n" % len(raw) + raw)
            stdin.flush()
        return req_id

    def respond(req_id: object, result: object) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode()
        with lock:
            stdin.write(b"Content-Length: %d\r\n\r\n" % len(raw) + raw)
            stdin.flush()

    def reader() -> None:
        while True:
            headers = {}
            line = stdout.readline()
            if not line:
                log("!! server stdout closed")
                return
            while line and line.strip():
                key, _, value = line.decode().partition(":")
                headers[key.strip().lower()] = value.strip()
                line = stdout.readline()
            length = int(headers.get("content-length", 0))
            if not length:
                continue
            msg = json.loads(stdout.read(length))
            method = msg.get("method", "")
            if method in ("window/logMessage", "window/showMessage"):
                log(f"  {method.split('/')[1]}: {msg['params'].get('message', '')[:300]}")
            elif method == "$/progress":
                value_obj = msg["params"].get("value", {})
                log(
                    f"  progress[{msg['params'].get('token')}] {value_obj.get('kind')}: "
                    f"{value_obj.get('title', '')} {value_obj.get('message', '')} {value_obj.get('percentage', '')}"
                )
            elif method == "workspace/configuration":
                items = msg["params"].get("items", [])
                respond(msg["id"], [{"csharp": {"logLevel": "info"}} for _ in items])
            elif method and "id" in msg:
                respond(msg["id"], None)
            elif "id" in msg:
                responses[msg["id"]] = msg

    threading.Thread(target=reader, daemon=True).start()

    init_id = send(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": project_root.as_uri(),
            "capabilities": {
                "textDocument": {
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "references": {},
                    "definition": {},
                },
                "window": {"workDoneProgress": True},
                "workspace": {"configuration": True},
            },
            "initializationOptions": {"csharp": {"logLevel": "info"}},
        },
        True,
    )
    while init_id not in responses:
        time.sleep(0.05)
    log("initialize response received (request 1)")
    send("initialized", {}, False)
    send("workspace/didChangeConfiguration", {"settings": {"csharp": {"logLevel": "info"}}}, False)

    probe_id = send("textDocument/documentSymbol", {"textDocument": {"uri": first_file.as_uri()}}, True)
    log(f"documentSymbol sent (request {probe_id}) — waiting up to {timeout}s ...")
    deadline = time.monotonic() + timeout
    while probe_id not in responses:
        if time.monotonic() > deadline:
            log(f"TIMEOUT: no documentSymbol response after {timeout}s  << reproduces customer failure")
            proc.kill()
            return 1
        if proc.poll() is not None:
            log(f"!! csharp-ls exited with code {proc.returncode}")
            return 2
        time.sleep(0.25)
    symbols = responses[probe_id].get("result") or []
    log(f"documentSymbol answered: {len(symbols)} symbols  << solution load completed")
    proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
