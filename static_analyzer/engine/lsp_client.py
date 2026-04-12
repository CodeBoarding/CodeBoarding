"""Synchronous LSP client using JSON-RPC over stdio.

Enhanced with diagnostics collection and server-ready wait support
for CodeBoarding integration.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from static_analyzer.engine.utils import uri_to_path
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap, LSPDiagnostic

logger = logging.getLogger(__name__)

LSP_METHOD_NOT_FOUND = -32601


class MethodNotFoundError(Exception):
    """Raised when the LSP server does not support a requested method."""


class LSPClient:
    """A synchronous Language Server Protocol client communicating over stdio.

    Uses a background thread to read LSP messages from stdout, so that
    response reading never blocks on IO — it only blocks on a queue with a timeout.

    Enhanced features:
    - Collects textDocument/publishDiagnostics notifications
    - Tracks language/status notifications for JDTLS import-wait
    """

    def __init__(
        self,
        command: list[str],
        project_root: Path,
        init_options: dict | None = None,
        default_timeout: int = 60,
        collect_diagnostics: bool = False,
        extra_env: dict[str, str] | None = None,
        workspace_settings: dict | None = None,
        extra_client_capabilities: dict | None = None,
    ) -> None:
        self._command = command
        self._project_root = project_root
        self._init_options = init_options or {}
        self._default_timeout = default_timeout
        self._collect_diagnostics = collect_diagnostics
        self._extra_env = extra_env or {}
        self._workspace_settings = workspace_settings
        # Adapter-specific keys merged into capabilities at ``initialize`` time.
        self._extra_client_capabilities = extra_client_capabilities or {}
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._stdout_fd: int | None = None
        self._request_id = 0
        self._msg_queue: queue.Queue[dict] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()
        self._write_lock = threading.Lock()

        # Track opened documents and their version counters.
        self._opened_uris: set[str] = set()
        self._doc_versions: dict[str, int] = {}

        # Diagnostics collection
        self._diagnostics: FileDiagnosticsMap = {}
        self._diagnostics_lock = threading.Lock()
        self._diagnostics_generation: int = 0

        # JDTLS import tracking
        self._server_ready = threading.Event()
        # Set when ``initialize`` returned an error response. ``wait_for_server_ready``
        # bails out immediately when this is set so callers don't burn 5 minutes
        # waiting on an LSP that already reported a fatal startup error
        # (e.g. csharp-ls failing to locate the .NET SDK).
        self._init_failed: bool = False

    def __enter__(self) -> LSPClient:
        self.start()
        return self

    def __exit__(self, _exc_type: type | None, _exc_val: Exception | None, _exc_tb: object | None) -> None:
        self.shutdown()

    def start(self) -> dict | list | None:
        """Start the LSP server process and perform initialization handshake."""
        env = os.environ.copy()
        env.update(self._extra_env)
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(self._project_root),
        )

        # Grab raw fd and close Python's BufferedReader immediately
        self._stdout_fd = os.dup(self._process.stdout.fileno())  # type: ignore[union-attr]
        self._process.stdout.close()  # type: ignore[union-attr]
        self._process.stdout = None  # type: ignore[assignment]

        # Start background reader
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        root_uri = self._project_root.as_uri()

        text_doc_capabilities: dict = {
            "documentSymbol": {
                "hierarchicalDocumentSymbolSupport": True,
            },
            "references": {},
            "definition": {},
            "typeHierarchy": {},
            "implementation": {},
            "callHierarchy": {},
        }
        if self._collect_diagnostics:
            text_doc_capabilities["publishDiagnostics"] = {
                "relatedInformation": True,
                "versionSupport": True,
                "tagSupport": {"valueSet": [1, 2]},
            }

        capabilities: dict = {"textDocument": text_doc_capabilities}
        # Shallow-merge adapter extras into the top-level capabilities. On
        # collision: dicts merge, scalars are overwritten by the adapter.
        for cap_key, cap_value in self._extra_client_capabilities.items():
            if cap_key in capabilities and isinstance(capabilities[cap_key], dict) and isinstance(cap_value, dict):
                capabilities[cap_key] = {**capabilities[cap_key], **cap_value}
            else:
                capabilities[cap_key] = cap_value

        init_result = self._send_request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "rootPath": str(self._project_root),
                "capabilities": capabilities,
                "workspaceFolders": [
                    {"uri": root_uri, "name": self._project_root.name},
                ],
                "initializationOptions": self._init_options,
            },
        )

        self._send_notification("initialized", {})

        # LSP servers receive configuration through three mechanisms:
        #   1. initializationOptions  — sent in the initialize request above.
        #   2. workspace/didChangeConfiguration — push notification from client.
        #   3. workspace/configuration — pull request initiated by the server
        #      (handled in _handle_server_request).
        #
        # Many servers only act on (2) or (3) and silently ignore (1) for
        # certain settings.  For example, Pyright needs
        # diagnosticSeverityOverrides via (2) to emit diagnostic codes in
        # publishDiagnostics, and gopls fetches analyzer config via (3).
        # We therefore deliver workspace_settings through both (2) and (3)
        # to ensure every server picks them up regardless of which
        # mechanism it prefers.
        if self._workspace_settings:
            self._send_notification(
                "workspace/didChangeConfiguration",
                {"settings": self._workspace_settings},
            )

        return init_result

    def shutdown(self) -> None:
        """Send shutdown request and exit notification, then terminate process."""
        self._shutdown_event.set()
        if self._process and self._process.poll() is None:
            try:
                self._send_request("shutdown", None, timeout=5)
                self._send_notification("exit", None)
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except Exception:
                    pass
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        if self._stdout_fd is not None:
            try:
                os.close(self._stdout_fd)
            except OSError:
                pass
            self._stdout_fd = None
        self._opened_uris.clear()
        self._doc_versions.clear()

    # ---- Document management ----

    def did_open(self, file_path: Path, language_id: str) -> None:
        """Notify the server that a document was opened.

        Idempotent: silently skips if the file is already open, since the
        LSP spec forbids duplicate didOpen notifications for the same URI.
        """
        uri = file_path.resolve().as_uri()
        if uri in self._opened_uris:
            return
        try:
            text = file_path.read_text(errors="replace")
        except Exception:
            text = ""
        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                },
            },
        )
        self._opened_uris.add(uri)
        self._doc_versions[uri] = 1

    def did_change(self, file_path: Path, content: str) -> None:
        """Notify the server that a document's content has changed."""
        uri = file_path.resolve().as_uri()
        version = self._doc_versions.get(uri, 1) + 1
        self._doc_versions[uri] = version
        self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            },
        )

    def did_close(self, file_path: Path) -> None:
        """Notify the server that a document was closed."""
        uri = file_path.resolve().as_uri()
        self._send_notification(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )
        self._opened_uris.discard(uri)
        self._doc_versions.pop(uri, None)

    # ---- LSP queries ----

    def document_symbol(self, file_path: Path, timeout: int | None = None) -> list[dict]:
        """Request document symbols for a file."""
        result = self._send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_path.resolve().as_uri()}},
            timeout=timeout,
        )
        if isinstance(result, list):
            return result
        return []

    def references(self, file_path: Path, line: int, character: int) -> list[dict]:
        """Find all references to the symbol at the given position."""
        result = self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_path.resolve().as_uri()},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            },
        )
        if isinstance(result, list):
            return result
        return []

    def send_references_batch(
        self, queries: list[tuple[Path, int, int]], per_query_timeout: int = 0
    ) -> tuple[list[list[dict]], set[int]]:
        """Send multiple references requests without waiting between them.

        Returns ``(results, error_indices)`` where *results* is a list of
        result lists (one per query, same order) and *error_indices* is a set
        of 0-based indices whose LSP responses were errors.

        Args:
            queries: List of (file_path, line, character) tuples.
            per_query_timeout: Per-query timeout in seconds. When > 0, the batch
                deadline is ``per_query_timeout * len(queries)`` instead of the
                default timeout. Use this for servers that serialize requests
                internally (e.g. JDTLS) where total time scales linearly.
        """
        timeout = per_query_timeout * len(queries) if per_query_timeout > 0 else None

        def build_params(file_path: Path, line: int, character: int) -> dict:
            return {
                "textDocument": {"uri": file_path.resolve().as_uri()},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            }

        return self._send_batch("textDocument/references", queries, build_params, timeout=timeout)

    def definition(self, file_path: Path, line: int, character: int, timeout: int | None = None) -> list[dict]:
        """Find the definition of the symbol at the given position."""
        result = self._send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_path.resolve().as_uri()},
                "position": {"line": line, "character": character},
            },
            timeout=timeout,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    def send_definition_batch(
        self, queries: list[tuple[Path, int, int]], timeout: int | None = None
    ) -> tuple[list[list[dict]], set[int]]:
        """Send multiple definition requests without waiting between them.

        Returns ``(results, error_indices)`` — see :meth:`send_references_batch`.
        """
        return self._send_batch("textDocument/definition", queries, self._position_params, timeout=timeout)

    def implementation(self, file_path: Path, line: int, character: int, timeout: int | None = None) -> list[dict]:
        """Find implementations of the symbol at the given position."""
        result = self._send_request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": file_path.resolve().as_uri()},
                "position": {"line": line, "character": character},
            },
            timeout=timeout,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    def send_implementation_batch(
        self, queries: list[tuple[Path, int, int]], timeout: int | None = None
    ) -> tuple[list[list[dict]], set[int]]:
        """Send multiple implementation requests without waiting between them.

        Returns ``(results, error_indices)`` — see :meth:`send_references_batch`.
        """
        return self._send_batch("textDocument/implementation", queries, self._position_params, timeout=timeout)

    def type_hierarchy_prepare(self, file_path: Path, line: int, character: int) -> list[dict] | None:
        """Prepare type hierarchy at the given position."""
        result = self._send_request(
            "textDocument/prepareTypeHierarchy",
            {
                "textDocument": {"uri": file_path.resolve().as_uri()},
                "position": {"line": line, "character": character},
            },
        )
        if isinstance(result, list):
            return result
        return None

    def type_hierarchy_supertypes(self, item: dict) -> list[dict]:
        """Get supertypes for a type hierarchy item."""
        result = self._send_request("typeHierarchy/supertypes", {"item": item})
        if isinstance(result, list):
            return result
        return []

    def type_hierarchy_subtypes(self, item: dict) -> list[dict]:
        """Get subtypes for a type hierarchy item."""
        result = self._send_request("typeHierarchy/subtypes", {"item": item})
        if isinstance(result, list):
            return result
        return []

    # ---- Diagnostics collection ----

    def get_collected_diagnostics(self) -> FileDiagnosticsMap:
        """Return diagnostics collected during analysis."""
        with self._diagnostics_lock:
            return dict(self._diagnostics)

    def get_diagnostics_generation(self) -> int:
        """Return a counter bumped on each ``publishDiagnostics`` notification."""
        with self._diagnostics_lock:
            return self._diagnostics_generation

    def wait_for_diagnostics_quiesce(self, idle_seconds: float, max_wait: float) -> None:
        """Block until ``publishDiagnostics`` stops arriving for ``idle_seconds`` (or ``max_wait`` elapses).

        Why: csharp-ls (and similar batching servers) emit diagnostics
        asynchronously after didOpen/load, often *after* the analysis pipeline
        has already snapshotted them. Other LSPs (pyright, gopls) publish
        eagerly and don't need this. Adapters opt in via
        ``diagnostics_quiesce_seconds``.
        """
        if idle_seconds <= 0 or max_wait <= 0:
            return
        deadline = time.monotonic() + max_wait
        last_gen = self.get_diagnostics_generation()
        last_change = time.monotonic()
        poll_interval = 0.1
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            gen = self.get_diagnostics_generation()
            now = time.monotonic()
            if gen != last_gen:
                last_gen = gen
                last_change = now
                continue
            if now - last_change >= idle_seconds:
                return

    # ---- JDTLS server-ready wait ----

    def wait_for_server_ready(self, timeout: int = 300) -> bool:
        """Wait for the LSP server to signal readiness (e.g., JDTLS project import).

        Blocks until a language/status notification with type 'ServiceReady' or
        'ProjectStatus' with message 'OK' is received, or until timeout.

        Args:
            timeout: Maximum time to wait in seconds (default: 5 minutes)

        Returns:
            ``True`` if the server signalled readiness, ``False`` on timeout
            or if initialization had already failed.
        """
        if self._init_failed:
            logger.warning(
                "Skipping ready-wait: LSP initialize errored. Subsequent requests will return empty results."
            )
            return False
        logger.info("Waiting for LSP server to be ready...")
        if self._server_ready.wait(timeout=timeout):
            logger.info("Server ready")
            return True
        logger.warning("Server ready timeout after %ds. Proceeding with analysis anyway.", timeout)
        return False

    def reset_ready_signal(self) -> None:
        """Clear the server-ready flag so the next ``wait_for_server_ready``
        blocks until the LSP server emits its readiness notification again.

        Used by adapters whose LSP transitions out of the ready state during
        post-didOpen processing (e.g. rust-analyzer flips ``quiescent`` back
        to ``False`` while ``cargo check`` runs, then back to ``True`` once
        diagnostics are flushed).
        """
        self._server_ready.clear()

    # ---- Internal protocol implementation ----

    def _position_params(self, file_path: Path, line: int, character: int) -> dict:
        """Build standard position-based LSP params (used by definition/implementation)."""
        return {
            "textDocument": {"uri": file_path.resolve().as_uri()},
            "position": {"line": line, "character": character},
        }

    def _send_batch(
        self,
        method: str,
        queries: list[tuple[Path, int, int]],
        build_params: Callable[[Path, int, int], dict],
        timeout: int | None = None,
    ) -> tuple[list[list[dict]], set[int]]:
        """Send multiple LSP requests and collect results in order.

        Generic batch helper that eliminates duplication across
        send_references_batch, send_definition_batch, and send_implementation_batch.

        Returns ``(parsed_results, error_indices)`` where *error_indices*
        is a set of 0-based query positions that received LSP errors.
        """
        req_ids: list[int] = []
        for file_path, line, character in queries:
            self._request_id += 1
            req_id = self._request_id
            req_ids.append(req_id)
            message: dict = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": build_params(file_path, line, character),
            }
            self._write_message(message)

        results, _, error_ids = self._collect_batch_responses(req_ids, timeout=timeout)

        error_indices: set[int] = set()
        for i, rid in enumerate(req_ids):
            if rid in error_ids:
                error_indices.add(i)

        parsed: list[list[dict]] = []
        for rid in req_ids:
            raw = results.get(rid, [])
            if isinstance(raw, dict):
                parsed.append([raw])
            elif isinstance(raw, list):
                parsed.append(raw)
            else:
                parsed.append([])
        return parsed, error_indices

    def _send_request(self, method: str, params: dict | list | None, timeout: int | None = None) -> dict | list | None:
        """Send a JSON-RPC request and wait for the response."""
        if timeout is None:
            timeout = self._default_timeout
        self._request_id += 1
        req_id = self._request_id

        message: dict = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        self._write_message(message)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._next_response(deadline)
            if msg is None:
                continue
            if msg.get("id") != req_id:
                continue

            if "error" in msg:
                error = msg["error"]
                if isinstance(error, dict) and error.get("code") == LSP_METHOD_NOT_FOUND:
                    raise MethodNotFoundError(error.get("message", "Method not found"))
                logger.warning("LSP error for request %d (%s): %s", req_id, method, error)
                if method == "initialize":
                    # Initialize errored — the server is not in a usable state.
                    # Mark so wait_for_server_ready() bails out fast instead of
                    # blocking for the full 300s readiness timeout.
                    self._init_failed = True
                return None
            return msg.get("result")

        raise TimeoutError(f"Timeout waiting for LSP response to request {req_id}")

    def _send_notification(self, method: str, params: dict | list | None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        message: dict = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(message)

    def _write_message(self, message: dict) -> None:
        """Write a JSON-RPC message with Content-Length header.

        Thread-safe — the reader thread may write responses to server-
        initiated requests concurrently with the main thread.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("LSP server not running")
        body = json.dumps(message)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        data = (header + body).encode("utf-8")
        with self._write_lock:
            self._process.stdin.write(data)
            self._process.stdin.flush()

    def _next_response(self, deadline: float) -> dict | None:
        """Dequeue the next response message, handling protocol housekeeping.

        Returns None on timeout/empty. Handles server-initiated requests
        and skips stray notifications automatically.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        try:
            message = self._msg_queue.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            if self._process and self._process.poll() is not None:
                raise RuntimeError(f"LSP server process exited with code {self._process.returncode}") from None
            return None

        # Skip notifications that leaked past the reader loop
        if "id" not in message:
            return None

        return message

    def _collect_batch_responses(
        self, request_ids: list[int], timeout: int | None = None
    ) -> tuple[dict[int, list[dict]], set[int], set[int]]:
        """Collect responses for multiple pending request IDs.

        Returns a tuple of (results, timed_out_ids, error_ids):
        - results: dict mapping request_id -> result list
        - timed_out_ids: set of request IDs that did not complete in time
        - error_ids: set of request IDs that returned LSP errors
        """
        if timeout is None:
            timeout = self._default_timeout

        results: dict[int, list[dict]] = {}
        pending = set(request_ids)
        error_ids: set[int] = set()
        error_messages: dict[str, int] = {}
        deadline = time.monotonic() + timeout

        while pending and time.monotonic() < deadline:
            msg = self._next_response(deadline)
            if msg is None:
                continue

            msg_id = msg.get("id")
            if msg_id not in pending:
                continue

            pending.discard(msg_id)  # type: ignore[arg-type]

            if "error" in msg:
                error_ids.add(msg_id)  # type: ignore[arg-type]
                err = msg["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                error_messages[err_msg] = error_messages.get(err_msg, 0) + 1
                results[msg_id] = []  # type: ignore[index]
            else:
                results[msg_id] = msg.get("result") or []  # type: ignore[index]

        for err_msg, count in error_messages.items():
            if count > 1:
                logger.warning("LSP error (x%d): %s", count, err_msg)
            else:
                logger.warning("LSP error: %s", err_msg)

        timed_out = set(pending)
        for req_id in pending:
            logger.warning("Timeout waiting for references request %d", req_id)
            results[req_id] = []

        return results, timed_out, error_ids

    # ---- Background message reader ----

    def _reader_loop(self) -> None:
        """Background thread: continuously read messages and enqueue them.

        Notifications and server-initiated requests are handled inline
        and NOT enqueued — only responses go on the queue.  Server
        requests (e.g. client/registerCapability) must be answered
        immediately because some servers (csharp-ls) block on the
        response before continuing workspace initialization.
        """
        while not self._shutdown_event.is_set():
            if not self._process or self._stdout_fd is None:
                break
            if self._process.poll() is not None:
                break
            try:
                msg = self._read_single_message()
                if msg is None:
                    break

                method = msg.get("method", "")
                if "id" not in msg and method:
                    # Pure notification — handle and discard, don't enqueue
                    self._handle_notification(method, msg.get("params", {}))
                    continue

                # Server-initiated request — respond immediately so the
                # server isn't blocked waiting for our reply.
                if "id" in msg and method:
                    result = self._handle_server_request(msg)
                    self._write_message({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                    continue

                self._msg_queue.put(msg)
            except Exception as e:
                if not self._shutdown_event.is_set():
                    logger.debug("Reader loop error: %s", e)
                break

    def _handle_server_request(self, message: dict) -> object:
        """Respond to server-initiated requests.

        LSP servers may send requests like ``workspace/configuration`` to
        fetch client-side settings.  gopls, for example, sends one
        ``workspace/configuration`` request per workspace folder with
        ``items: [{"section": "gopls"}]`` and expects an array of flat
        settings objects back.
        """
        method = message.get("method", "")
        if method == "workspace/configuration" and self._workspace_settings:
            items = message.get("params", {}).get("items", [])
            # Return one settings object per requested item.
            return [self._workspace_settings for _ in items] if items else [self._workspace_settings]
        return None

    def _handle_notification(self, method: str, params: dict) -> None:
        """Process LSP notifications for diagnostics and server status."""
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics", [])
            if uri:
                file_path = uri_to_path(uri)
                if file_path:
                    file_key = str(file_path.resolve())
                    lsp_diags = [LSPDiagnostic.from_lsp_dict(d) for d in diagnostics]
                    with self._diagnostics_lock:
                        self._diagnostics[file_key] = lsp_diags
                        self._diagnostics_generation += 1

        elif method == "language/status":
            status_type = params.get("type", "")
            status_message = params.get("message", "")
            logger.debug("LSP status: type=%s, message=%s", status_type, status_message)

            if status_type == "ServiceReady":
                self._server_ready.set()
                logger.info("LSP server: ServiceReady")
            elif status_type == "ProjectStatus" and status_message == "OK":
                self._server_ready.set()
                logger.info("LSP server: ProjectStatus OK")

        elif method == "experimental/serverStatus":
            # rust-analyzer's readiness signal. ``quiescent`` flips True once
            # the initial workspace load completes; we set ready then
            # regardless of health (a ``warning`` state still has a usable
            # reference index, just with diagnostics flagged).
            quiescent = bool(params.get("quiescent", False))
            health = params.get("health", "")
            logger.debug("rust-analyzer status: health=%s, quiescent=%s", health, quiescent)
            if quiescent:
                self._server_ready.set()
                logger.info("LSP server: rust-analyzer quiescent (health=%s)", health)

        elif method == "window/logMessage":
            message_text = params.get("message", "")
            # csharp-ls signals workspace readiness via logMessage
            if "Finished loading solution" in message_text:
                self._server_ready.set()
                logger.info("LSP server: solution loaded (%s)", message_text)

        elif method == "$/progress":
            # csharp-ls reports csproj-based workspace load completion via
            # work-done progress (not logMessage). The "End" message looks
            # like ``OK, N project file(s) loaded``.
            value = params.get("value", {}) or {}
            kind = value.get("kind")
            message_text = value.get("message", "") or ""
            if kind == "end" and "project file(s) loaded" in message_text:
                self._server_ready.set()
                logger.info("LSP server: project files loaded (%s)", message_text)

    def _read_single_message(self) -> dict | None:
        """Read a single JSON-RPC message from stdout using raw fd I/O."""
        fd = self._stdout_fd
        if fd is None:
            return None

        content_length = None
        header_buf = bytearray()

        # Read header byte-by-byte to avoid reading past message boundaries
        while True:
            try:
                byte = os.read(fd, 1)
            except OSError:
                return None
            if not byte:
                return None
            header_buf.append(byte[0])

            if len(header_buf) >= 4 and header_buf[-4:] == b"\r\n\r\n":
                break

        header_str = header_buf.decode("utf-8", errors="replace")
        for line in header_str.strip().split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())

        if content_length is None:
            return None

        body = bytearray()
        while len(body) < content_length:
            try:
                chunk = os.read(fd, content_length - len(body))
            except OSError:
                return None
            if not chunk:
                return None
            body.extend(chunk)

        return json.loads(body.decode("utf-8", errors="replace"))
