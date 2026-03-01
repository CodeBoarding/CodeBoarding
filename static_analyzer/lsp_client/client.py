import json
import logging
import os
import re
import subprocess
import threading
import time
from abc import ABC
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pathspec
from tqdm import tqdm

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.graph import CallGraph, Node
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap, LSPDiagnostic
from static_analyzer.lsp_client.language_settings import get_language_settings
from static_analyzer.scanner import ProgrammingLanguage

# Configure logging
logger = logging.getLogger(__name__)


def uri_to_path(file_uri: str) -> Path:
    """
    Convert a file:// URI to a Path object, handling cross-platform differences.

    On Unix: file:///path/to/file -> /path/to/file
    On Windows: file:///C:/path/to/file -> C:/path/to/file

    Args:
        file_uri: A file:// URI string

    Returns:
        Path object representing the file path
    """
    parsed = urlparse(file_uri)
    path_str = url2pathname(unquote(parsed.path))
    return Path(path_str)


@dataclass
class FileAnalysisResult:
    """Container for single file analysis results"""

    file_path: Path
    package_name: str
    imports: list[str]
    symbols: list[Node]
    function_symbols: list[dict]
    class_symbols: list[dict]
    call_relationships: list[tuple]  # (caller_qualified_name, callee_qualified_name)
    class_hierarchies: dict[str, dict]
    outgoing_edges: list[tuple] = field(default_factory=list)
    incoming_edges: list[tuple] = field(default_factory=list)
    body_edges: list[tuple] = field(default_factory=list)
    method_timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None


class LSPClient(ABC):
    """
    Language server protocol client for interacting with langservers.

    This is an abstract base class that provides common LSP functionality.
    Subclasses should override handle_notification() if they need to process
    language server notifications (e.g., for tracking build/import progress).
    """

    # Known LSP server-to-client request methods that we handle
    # See: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#serverToClientRequest
    SERVER_REQUEST_METHODS = {
        "workspace/configuration",  # Request for workspace configuration
        "window/workDoneProgress/create",  # Request to create progress token
        "client/registerCapability",  # Request to register capabilities dynamically
        "window/showMessageRequest",  # Request to show message with actions
        "window/showDocument",  # Request to show a document
    }

    def __init__(
        self,
        project_path: Path,
        language: ProgrammingLanguage,
        ignore_manager: RepoIgnoreManager | None = None,
    ):
        """
        Initializes the client and starts the langserver process.
        """
        self.project_path = project_path
        if not self.project_path.is_dir():
            raise ValueError(f"Project path '{project_path}' does not exist or is not a directory.")

        self.language = language
        self.server_start_params = language.get_server_parameters()
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._shutdown_flag = threading.Event()
        self.language_suffix_pattern = language.get_suffix_pattern()
        self.language_id: str = language.get_language_id()

        self._message_id = 1
        self._responses: dict[int, dict] = {}
        self._notifications: list[dict] = []
        self._lock = threading.RLock()
        self._response_condition = threading.Condition(self._lock)

        # Initialize CallGraph
        self.call_graph = CallGraph()
        self.symbol_kinds = list(range(1, 27))  # all types from the LSP for now
        self.ignore_manager = ignore_manager if ignore_manager else RepoIgnoreManager(self.project_path)

        # Initialize diagnostics collection for health checks
        self.diagnostics: FileDiagnosticsMap = {}

        # Track per-document versions for textDocument/didChange notifications
        self._document_versions: dict[str, int] = {}

        # Maps file URI -> {selection_range_line -> (qualified_name, sel_char)} for all workspace
        # symbols.  Built before parallel analysis so that _process_definition_response can resolve
        # method definitions to their class-qualified names (e.g. core.base.Dog.create).
        # The sel_char is stored so we can detect when a definition points to a same-line parameter
        # rather than the function itself (e.g. `def log_call(func):` — both log_call and func are
        # on the same line but at different columns).
        self._definition_location_map: dict[str, dict[int, tuple[str, int]]] = {}

    def start(self):
        """Starts the language server process and the message reader thread."""
        logger.info(f"Starting server {' '.join(self.server_start_params)}...")
        self._process = subprocess.Popen(
            self.server_start_params,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self._reader_thread = threading.Thread(target=self._read_messages)
        self._reader_thread.daemon = True
        self._reader_thread.start()
        self._initialize()
        self._send_workspace_configuration()

    def _send_request(self, method: str, params: dict):
        """Sends a JSON-RPC request to the server."""
        with self._lock:
            message_id = self._message_id
            self._message_id += 1

            request = {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": method,
                "params": params,
            }

            body = json.dumps(request)
            message = f"Content-Length: {len(body)}\r\n\r\n{body}"

            if self._process and self._process.stdin:
                self._process.stdin.write(message.encode("utf-8"))
                self._process.stdin.flush()

        return message_id

    def _send_notification(self, method: str, params: dict):
        """Sends a JSON-RPC notification to the server."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        body = json.dumps(notification)
        message = f"Content-Length: {len(body)}\r\n\r\n{body}"

        with self._lock:
            if self._process and self._process.stdin:
                self._process.stdin.write(message.encode("utf-8"))
                self._process.stdin.flush()

    def _read_messages(self):
        """
        Runs in a separate thread to read and process messages from the server's stdout.
        """
        while not self._shutdown_flag.is_set():
            try:
                if not self._process or not self._process.stdout:
                    break
                line = self._process.stdout.readline().decode("utf-8")
                if not line or not line.startswith("Content-Length"):
                    continue

                content_length = int(line.split(":")[1].strip())
                self._process.stdout.readline()  # Read the blank line

                body = self._process.stdout.read(content_length).decode("utf-8")
                response = json.loads(body)

                if "id" in response and "method" in response:
                    # Server-to-client REQUEST (has both id and method)
                    # This is distinct from:
                    # - Responses (have id, no method)
                    # - Notifications (have method, no id)
                    self._handle_server_request(response)
                elif "id" in response:
                    # Response to our request (has id, no method)
                    with self._response_condition:
                        self._responses[response["id"]] = response
                        self._response_condition.notify_all()
                else:
                    # Notification from server (has method, no id)
                    with self._lock:
                        self._notifications.append(response)
                    # Process notification immediately
                    self._handle_notification(response)

            except (IOError, ValueError) as e:
                if not self._shutdown_flag.is_set():
                    logger.error(f"Error reading from server: {e}")
                break

    def _wait_for_response(self, message_id: int, timeout: int = 60):
        """Waits for a response with a specific message ID to arrive."""
        deadline = time.monotonic() + timeout
        with self._response_condition:
            while message_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for response to message {message_id}, after {timeout} seconds."
                    )
                self._response_condition.wait(timeout=remaining)
            return self._responses.pop(message_id)

    def _handle_notification(self, notification: dict):
        """
        Process a notification from the LSP server.

        This method extracts the method and params from the notification and calls
        the subclass's handle_notification() method. It also captures diagnostics
        for health checks.

        Args:
            notification: The notification dictionary from the LSP server
        """
        method = notification.get("method", "")
        params = notification.get("params", {})

        # Capture diagnostics for health checks
        if method == "textDocument/publishDiagnostics":
            self._handle_diagnostics_notification(params)

        # Call subclass handler
        try:
            self.handle_notification(method, params)
        except Exception as e:
            logger.debug(f"Error in notification handler for {method}: {e}")

    def _handle_diagnostics_notification(self, params: dict):
        """Handle textDocument/publishDiagnostics notifications.

        Stores diagnostics for later use in health checks.

        Args:
            params: The notification parameters containing uri and diagnostics
        """
        try:
            uri = params.get("uri", "")
            raw_diagnostics = params.get("diagnostics", [])

            if not uri or not raw_diagnostics:
                return

            # Convert URI to file path
            file_path = str(uri_to_path(uri))
            new_diags = [LSPDiagnostic.from_lsp_dict(d) for d in raw_diagnostics]

            with self._lock:
                existing_diags = self.diagnostics.get(file_path, [])
                all_diags = existing_diags + new_diags

                seen: set[tuple[str, str, int, int]] = set()
                unique_diags: list[LSPDiagnostic] = []
                for diag in all_diags:
                    key = diag.dedup_key()
                    if key not in seen:
                        seen.add(key)
                        unique_diags.append(diag)

                self.diagnostics[file_path] = unique_diags
        except Exception as e:
            logger.debug(f"Error handling diagnostics notification: {e}")

    def get_collected_diagnostics(self) -> FileDiagnosticsMap:
        """Get all collected diagnostics.

        Returns:
            Dictionary mapping file paths to lists of LSPDiagnostic objects
        """
        with self._lock:
            return self.diagnostics.copy()

    def handle_notification(self, method: str, params: dict):
        """
        Handle notifications from the LSP server.

        Subclasses can override this method to process specific notifications
        from the language server (e.g., build progress, import status, diagnostics).

        The default implementation does nothing. Override this method in subclasses
        that need to track server-side events.

        Args:
            method: The LSP notification method name (e.g., "language/status")
            params: The notification parameters

        Example:
            For Java (JDTLS), override to track project import progress:

                def handle_notification(self, method: str, params: dict):
                    if method == "language/status":
                        if params.get("type") == "ProjectStatus" and params.get("message") == "OK":
                            self.import_complete = True

            For TypeScript, the default implementation is sufficient (no override needed).
        """
        # Default implementation: do nothing
        # Subclasses override this to handle language-specific notifications
        pass

    def _handle_server_request(self, request: dict):
        """
        Handle requests from the LSP server that expect a response.

        These are distinct from notifications - they have both 'id' and 'method' fields
        and the server blocks until we respond.

        See: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
        """
        method = request.get("method", "")
        request_id = request.get("id")
        params = request.get("params", {})

        # Per LSP spec, id must be present and is either int or string
        if request_id is None:
            logger.warning(f"Received server request without id: {method}")
            return

        if method == "workspace/configuration":
            # Return empty configuration for each requested item
            items = params.get("items", [])
            result: list[dict] = [{} for _ in items]
            self._send_response(request_id, result)
        elif method == "window/workDoneProgress/create":
            # Acknowledge progress token creation
            self._send_response(request_id, None)
        elif method == "client/registerCapability":
            # Acknowledge capability registration
            self._send_response(request_id, None)
        elif method == "window/showMessageRequest":
            # Don't select any action, just acknowledge
            self._send_response(request_id, None)
        elif method == "window/showDocument":
            # Acknowledge but indicate we didn't show the document
            self._send_response(request_id, {"success": False})
        else:
            # UNKNOWN server request - log as warning so we can add handling if needed
            logger.warning(
                f"Unknown LSP server request received: {method} (id={request_id}). "
                f"Responding with null to unblock server. "
                f"Consider adding explicit handling for this request type."
            )
            self._send_response(request_id, None)

    def _send_response(self, request_id: int | str, result) -> None:
        """
        Send a response to a server request.

        Args:
            request_id: The request ID from the server (int or str per LSP spec)
            result: The response result to send back to the server
        """
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
        body = json.dumps(response)
        body_bytes = body.encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n"

        with self._lock:
            if self._process and self._process.stdin:
                self._process.stdin.write(header.encode("utf-8") + body_bytes)
                self._process.stdin.flush()

    def _initialize(self):
        """Performs the LSP initialization handshake."""
        logger.info(f"Initializing connection for {self.language_id}...")
        params = {
            "processId": os.getpid(),
            "rootUri": self.project_path.as_uri(),
            "capabilities": {
                "textDocument": {
                    "callHierarchy": {"dynamicRegistration": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "typeHierarchy": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "semanticTokens": {"dynamicRegistration": True},
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "versionSupport": True,
                        "tagSupport": {"valueSet": [1, 2]},  # 1=Unnecessary, 2=Deprecated
                    },
                }
            },
            "workspace": {
                "applyEdit": True,
                "workspaceEdit": {"documentChanges": True},
            },
        }

        # Add language-specific settings to enable unused code diagnostics
        settings = get_language_settings(self.language_id)
        if settings:
            # For Python/Pyright, add the project root as an extra search path so that
            # cross-package imports (e.g. `from utils.helpers import add`) resolve when
            # there is no virtual-environment or installed package.  The project root is
            # always a valid source root for first-party code.
            if self.language_id.lower() == "python":
                settings.setdefault("python", {}).setdefault("analysis", {}).setdefault("extraPaths", []).append(
                    str(self.project_path)
                )
            params["initializationOptions"] = settings
        init_id = self._send_request("initialize", params)
        # Use longer timeout for initialization as it may involve full workspace indexing
        response = self._wait_for_response(init_id, timeout=360)

        if "error" in response:
            raise RuntimeError(f"Initialization failed: {response['error']}")

        logger.info("Initialization successful.")
        self._send_notification("initialized", {})

    def _send_workspace_configuration(self):
        """Send workspace configuration to enable unused code detection.

        Some LSP servers (like Pyright) require configuration to be sent via
        workspace/didChangeConfiguration after initialization to enable
        features like unused code detection.
        """
        settings = get_language_settings(self.language_id)
        if not settings:
            return

        if self.language_id.lower() == "python":
            settings.setdefault("python", {}).setdefault("analysis", {}).setdefault("extraPaths", []).append(
                str(self.project_path)
            )

        logger.info(f"Sending workspace configuration for {self.language_id}...")

        # Send configuration using workspace/didChangeConfiguration
        self._send_notification(
            "workspace/didChangeConfiguration",
            {"settings": settings},
        )

        logger.info(f"Workspace configuration sent for {self.language_id}")

    def _get_document_symbols(self, file_uri: str) -> list:
        """Fetches all document symbols (functions, classes, etc.) for a file."""
        params = {"textDocument": {"uri": file_uri}}
        req_id = self._send_request("textDocument/documentSymbol", params)
        logger.debug(f"Requesting document symbols for {file_uri} with ID {req_id}")
        response = self._wait_for_response(req_id)
        return response.get("result", [])

    def _build_definition_location_map(self, src_files: list[Path]) -> None:
        """Build a mapping from file_uri -> {line -> (qualified_name, sel_char)} for all workspace symbols.

        This allows _process_definition_response to resolve method definitions to their
        class-qualified names (e.g. Dog.create -> core.base.Dog.create) without extra LSP round-trips
        during parallel analysis.  The map is stored in self._definition_location_map.
        The sel_char is stored alongside the name so that same-line symbols (e.g. a function and its
        parameter on the same `def` line) can be distinguished by column.
        """
        self._definition_location_map = {}
        for file_path in src_files:
            try:
                file_uri = file_path.as_uri()
                symbols = self._get_document_symbols(file_uri)
                file_map: dict[int, tuple[str, int]] = {}
                self._collect_symbol_lines(symbols, file_path, file_map, parent_name=None)
                self._definition_location_map[file_uri] = file_map
            except Exception as e:
                logger.debug(f"Error building definition location map for {file_path}: {e}")

    # LSP symbol kinds that represent callable/navigable entities worth indexing.
    # Class=5, Method=6, Function=12, Constructor=9.
    # Variables (13), parameters, fields, etc. are intentionally excluded to avoid
    # overwriting a method entry when a same-line parameter has the same selection line.
    _DEFINITION_MAP_SYMBOL_KINDS = {5, 6, 9, 12}

    # LSP symbol kind for Class.  Only class parents are included in the definition-map
    # qualified name so that class methods get their class-qualified name (e.g. Dog.create)
    # while nested functions keep their flat module-level name (e.g. wrapper, not log_call.wrapper).
    _CLASS_KIND = 5

    def _collect_symbol_lines(
        self,
        symbols: list,
        file_path: Path,
        file_map: dict[int, tuple[str, int]],
        parent_name: str | None,
        parent_kind: int = 0,
    ) -> None:
        """Recursively walk document symbols and populate file_map with line -> (qualified_name, sel_char).

        Only callable/navigable symbol kinds (class, method, function, constructor) are
        indexed.  Parameters and variables are skipped so that a method and its same-line
        parameter do not compete for the same map entry.
        The sel_char is stored so _process_definition_response can distinguish between a function
        and a parameter on the same `def` line (they share the line but differ in column).

        Parent context is only included in the qualified name when the parent is a class (kind=5).
        Nested functions inside other functions use their flat name (e.g. `wrapper`, not
        `log_call.wrapper`) so the definition map stays consistent with the flat node names
        produced by _create_qualified_name in the REFERENCES section.
        """
        for sym in symbols:
            name = sym.get("name", "")
            kind = sym.get("kind", 0)
            if not name:
                continue
            # Only propagate the parent prefix when the parent is a class, not a function.
            # This ensures class methods get class-qualified names (Dog.create) while nested
            # functions inside other functions keep their flat names (wrapper, not log_call.wrapper).
            effective_parent = parent_name if parent_kind == self._CLASS_KIND else None
            qualified_symbol = f"{effective_parent}.{name}" if effective_parent else name

            if kind in self._DEFINITION_MAP_SYMBOL_KINDS:
                # Map the selection range start line to (qualified_name, col) from project root.
                # Only write if this line hasn't been claimed yet (first writer wins).
                sel_range_start = sym.get("selectionRange", sym.get("range", {})).get("start", {})
                sel_line = sel_range_start.get("line")
                sel_char = sel_range_start.get("character", 0)
                if sel_line is not None and sel_line not in file_map:
                    # Avoid doubling the file stem in the qualified name.
                    # E.g. for Animal.java: "Animal.getName" → just "getName" before
                    # passing to _create_qualified_name, which will prepend "...Animal."
                    stem = file_path.stem
                    sym_for_name = qualified_symbol
                    if sym_for_name.startswith(f"{stem}."):
                        sym_for_name = sym_for_name[len(stem) + 1 :]
                    full_name = self._create_qualified_name(file_path, sym_for_name)
                    file_map[sel_line] = (full_name, sel_char)

            # Always recurse into children so nested methods/classes are indexed
            children = sym.get("children", [])
            if children:
                self._collect_symbol_lines(
                    children, file_path, file_map, parent_name=qualified_symbol, parent_kind=kind
                )

    def _prepare_call_hierarchy(self, file_uri: str, line: int, character: int) -> list:
        """Prepares a call hierarchy at a specific location."""
        params = {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        }
        req_id = self._send_request("textDocument/prepareCallHierarchy", params)
        response = self._wait_for_response(req_id, timeout=30)
        return response.get("result", [])

    def _get_incoming_calls(self, item: dict) -> list:
        """Gets incoming calls for a call hierarchy item."""
        req_id = self._send_request("callHierarchy/incomingCalls", {"item": item})
        response = self._wait_for_response(req_id, timeout=30)
        return response.get("result", [])

    def _get_outgoing_calls(self, item: dict) -> list:
        """Gets outgoing calls for a call hierarchy item."""
        req_id = self._send_request("callHierarchy/outgoingCalls", {"item": item})
        response = self._wait_for_response(req_id, timeout=30)
        return response.get("result", [])

    # Keywords to skip when scanning for call positions.
    _CALL_SCAN_KEYWORDS = {
        "if",
        "for",
        "while",
        "def",
        "class",
        "function",
        "return",
        "yield",
        "import",
        "from",
        "as",
        "with",
        "try",
        "except",
        "finally",
        "raise",
        "assert",
        "lambda",
        "pass",
        "break",
        "continue",
    }

    def _find_call_positions_in_range(self, content: str, start_line: int, end_line: int) -> list[dict]:
        """
        Find positions of function/method calls and callable references within a line range.

        Three patterns are captured:
        1. Direct calls:  identifier(  — the classic function-call pattern.
        2. Return refs:   return <identifier>  — returning a closure/inner function without calling it.
        3. Callable args: identifiers passed as standalone arguments (not followed by '('), which
           arise in higher-order function calls like filter(is_positive, ...) or map(double, ...).

        Returns list of: {'line': int, 'char': int, 'name': str}
        """
        lines = content.split("\n")
        call_positions = []
        seen: set[tuple[int, int]] = set()  # (line_idx, char) pairs already added

        def add_position(line_idx: int, char: int, name: str) -> None:
            if (line_idx, char) not in seen and name and not name[0].isdigit():
                if name not in self._CALL_SCAN_KEYWORDS:
                    seen.add((line_idx, char))
                    call_positions.append({"line": line_idx, "char": char, "name": name})

        for line_idx in range(start_line, min(end_line + 1, len(lines))):
            line = lines[line_idx]
            # --- Pattern 1: direct calls  identifier( ---
            for char_idx in range(len(line)):
                if line[char_idx] != "(":
                    continue

                # Work backwards to find the identifier before '('
                name_end = char_idx - 1
                while name_end >= 0 and line[name_end] in " \t":
                    name_end -= 1

                if name_end < 0:
                    continue

                name_start = name_end
                while name_start >= 0 and (line[name_start].isalnum() or line[name_start] == "_"):
                    name_start -= 1
                name_start += 1

                if name_start > name_end:
                    continue

                identifier = line[name_start : name_end + 1]

                # Skip declaration names like "def foo(" or "class Bar("
                decl_prefix = line[:name_start].rstrip()
                if decl_prefix.endswith(("def", "class", "function")):
                    continue

                add_position(line_idx, name_start, identifier)

            # --- Pattern 2: return <identifier>  (closure / inner-function reference) ---
            # Matches lines like `return wrapper` or `return multiplier` where the identifier
            # is NOT immediately followed by '(' (which would already be caught by Pattern 1).
            m = re.match(r"^\s*return\s+([A-Za-z_][A-Za-z0-9_]*)(\s*$|[^(\w])", line)
            if m:
                name = m.group(1)
                char = line.index(name, line.index("return") + len("return"))
                add_position(line_idx, char, name)

            # --- Pattern 3: standalone identifier arguments to function calls ---
            # Finds identifiers that appear as arguments (preceded by '(' or ',') and are
            # NOT followed by '(' — meaning they are passed as callable values, not called.
            # Example: filter(is_positive, values)  →  is_positive is a callable argument.
            for m in re.finditer(r"(?<=[,(])\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[,)])", line):
                name = m.group(1)
                if name in self._CALL_SCAN_KEYWORDS:
                    continue
                # Find the column of the identifier in the original line
                char = m.start(1)
                # Skip if already captured as a direct call (identifier followed by '(' elsewhere)
                # The add_position guard handles duplicates via the seen set.
                add_position(line_idx, char, name)

        return call_positions

    def _resolve_call_position(self, file_uri: str, file_path: Path, call_pos: dict) -> str | None:
        """
        Resolve a call position to its qualified name using textDocument/definition.
        Returns the qualified name or None if unresolved.
        """
        try:
            definitions = self._get_definition_for_position(file_uri, call_pos["line"], call_pos["char"])

            if not definitions:
                # Unresolved - might be builtin or external
                return None

            # Handle both single definition and list of definitions
            definition = definitions[0] if isinstance(definitions, list) else definitions

            def_uri = definition.get("uri", "")
            if not def_uri.startswith("file://"):
                return None

            def_path = uri_to_path(def_uri)

            # Check if path is within project
            try:
                def_path.relative_to(self.project_path)
                # Create qualified name for project files
                return self._create_qualified_name(def_path, call_pos["name"])
            except ValueError:
                # For external libraries or builtins, use simple name
                return call_pos["name"]

        except Exception as e:
            logger.debug(f"Error resolving call position '{call_pos.get('name', '')}': {e}")
            return None

    def _process_definition_response(self, definitions: list, file_path: Path, call_pos: dict) -> str | None:
        """
        Process a definition response and return the qualified name.
        This is the non-blocking version used by batched METHOD 3.
        """
        try:
            if not definitions:
                return None

            # Handle both single definition and list of definitions
            definition = definitions[0] if isinstance(definitions, list) else definitions

            def_uri = definition.get("uri", "")
            if not def_uri.startswith("file://"):
                return None

            def_path = uri_to_path(def_uri)

            # Check if path is within project
            try:
                def_path.relative_to(self.project_path)
            except ValueError:
                # For external libraries or builtins, skip the edge
                return None

            # Prefer looking up the qualified name from the pre-built definition location map.
            # This correctly handles methods nested inside classes
            # (e.g. Dog.create -> core.base.Dog.create instead of core.base.create).
            def_range_start = definition.get("range", {}).get("start", {})
            def_line = def_range_start.get("line")
            def_char = def_range_start.get("character", -1)
            if def_line is not None and def_uri in self._definition_location_map:
                entry = self._definition_location_map[def_uri].get(def_line)
                if entry is not None:
                    mapped_name, sel_char = entry
                    # Only use the mapped name when the definition character column matches
                    # the symbol's selection start column.  A mismatch means the definition
                    # points to a parameter on the same line as the function declaration
                    # (e.g. `def log_call(func):` — log_call is at col 4, func is at col 13).
                    # In that case fall through to the call-site name fallback below.
                    if def_char == sel_char:
                        return mapped_name

            # Fall back to the call-site name when the map has no entry or the character
            # column doesn't match (parameter vs. function on the same line).
            return self._create_qualified_name(def_path, call_pos["name"])

        except Exception as e:
            logger.debug(f"Error processing definition response: {e}")
            return None

    def _flatten_symbols(self, symbols: list):
        """Recursively flattens a list of hierarchical symbols."""
        flat_list = []
        for symbol in symbols:
            if symbol["kind"] in self.symbol_kinds:
                flat_list.append(symbol)
            if "children" in symbol:
                flat_list.extend(self._flatten_symbols(symbol["children"]))
        return flat_list

    def _get_source_files(self) -> list:
        """Get source files for this language. Override in subclasses for custom logic."""
        src_files = []
        for pattern in self.language_suffix_pattern:
            src_files.extend(list(self.project_path.rglob(pattern)))
        return self.filter_src_files(src_files)

    def _create_qualified_name(self, file_path: Path, symbol_name: str) -> str:
        """Create a fully qualified name for a symbol."""
        try:
            rel_path = file_path.relative_to(self.project_path)
            module_path = str(rel_path.with_suffix("")).replace(os.sep, ".")
            return f"{module_path}.{symbol_name}"
        except ValueError:
            # File is outside project root
            return f"{file_path.name}.{symbol_name}"

    def build_static_analysis(self, source_files_override: list[Path] | None = None) -> dict:
        """
        Unified method to build all static analysis data using multithreading.

        Returns:
            A dictionary containing:
            - 'call_graph': CallGraph object with all function call relationships
            - 'class_hierarchies': dict mapping class names to inheritance information
            - 'package_relations': dict mapping package names to their dependencies
            - 'references': list of Node objects for all symbols
        """
        logger.info("Starting unified static analysis with multithreading...")

        # Initialize data structures with thread-safe locks
        call_graph = CallGraph()
        class_hierarchies: dict[str, dict] = {}
        package_relations: dict[str, dict] = {}
        reference_nodes: list[Node] = []

        # Get source files and apply filters
        src_files = source_files_override if source_files_override is not None else self._get_source_files()
        src_files = self.filter_src_files(src_files)
        total_files = len(src_files)

        if not src_files:
            logger.warning("No source files found in the project.")
            return {
                "call_graph": call_graph,
                "class_hierarchies": class_hierarchies,
                "package_relations": package_relations,
                "references": reference_nodes,
            }

        logger.info(f"Found {total_files} source files. Starting unified analysis...")

        # Prepare for analysis (language-specific setup)
        self._prepare_for_analysis()

        # Pre-open all source files so the LSP server indexes them before any
        # textDocument/definition requests are made during parallel body analysis.
        # Without this, cross-file definition lookups race against didOpen
        # notifications from other threads, causing non-deterministic edge drops.
        for file_path in src_files:
            try:
                content = file_path.read_text(encoding="utf-8")
                self._send_notification(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": file_path.as_uri(),
                            "languageId": self.language_id,
                            "version": 1,
                            "text": content,
                        }
                    },
                )
            except Exception as e:
                logger.debug(f"Error pre-opening file {file_path}: {e}")

        # Wait for the LSP server to finish indexing all pre-opened files.
        # Many servers (e.g. Pyright) analyse files asynchronously after didOpen.
        # Requesting documentSymbols for each file acts as a synchronisation
        # barrier: the server can only respond once it has processed that file.
        # We do this for every file (not just the first) because some servers
        # (e.g. typescript-language-server) index files lazily on first access.
        for barrier_file in src_files:
            try:
                self._get_document_symbols(barrier_file.as_uri())
            except Exception as e:
                logger.debug(f"Error waiting for LSP indexing of {barrier_file}: {e}")

        # Build a workspace-wide map from definition location (file URI + selection line) to
        # qualified name.  This lets _process_definition_response resolve method references to
        # their class-qualified names (e.g. Dog.create -> core.base.Dog.create) without issuing
        # additional LSP requests during the parallel analysis phase.
        logger.info("Building definition location map...")
        self._build_definition_location_map(src_files)
        logger.info(f"Definition location map built for {len(self._definition_location_map)} files")

        # Get all classes in workspace for hierarchy analysis
        all_classes = self._get_all_classes_in_workspace()
        logger.info(f"Found {len(all_classes)} classes in workspace")

        cpu_count = os.cpu_count()
        max_workers = max(1, cpu_count - 1) if cpu_count else 1  # Use the number of cores but reserve one
        successful_results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all file analysis tasks
            future_to_file = {
                executor.submit(self._analyze_single_file, file_path, all_classes): file_path for file_path in src_files
            }

            # Collect results as they complete
            with tqdm(total=total_files, desc="[Unified Analysis] Processing files") as pbar:
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        # Add timeout to prevent infinite blocking if worker thread hangs
                        # 5 minutes should be more than enough for any single file
                        result = future.result(timeout=300)
                        if result.error:
                            logger.error(f"Error processing {file_path}: {result.error}")
                        else:
                            successful_results.append(result)
                    except TimeoutError:
                        logger.error(
                            f"Timeout (300s) processing {file_path} - worker thread may be hung on LSP request"
                        )
                    except Exception as e:
                        logger.error(f"Exception processing {file_path}: {e}")
                    finally:
                        pbar.update(1)

        logger.info(f"Successfully processed {len(successful_results)} files")

        # Allow time for final diagnostics to arrive from the LSP server
        # before closing files. Files were kept open during analysis to
        # give the server time to produce diagnostics.
        time.sleep(2)

        # Close all files that were opened during analysis
        for file_path in src_files:
            try:
                file_uri = file_path.as_uri()
                self._send_notification("textDocument/didClose", {"textDocument": {"uri": file_uri}})
            except Exception as e:
                logger.debug(f"Error closing file {file_path}: {e}")

        # Sort results by file path for deterministic graph construction.
        # as_completed() returns results in non-deterministic order which causes
        # downstream metrics to fluctuate between runs.
        successful_results.sort(key=lambda r: str(r.file_path))

        # First pass: add all nodes, package relations, and class hierarchies
        for result in successful_results:
            # 1. PACKAGE RELATIONS
            if result.package_name not in package_relations:
                package_relations[result.package_name] = {
                    "imports": set(),
                    "import_deps": set(),
                    "imported_by": set(),
                    "files": [],
                }
            package_relations[result.package_name]["files"].append(str(result.file_path))

            for imported_module in result.imports:
                imported_package = self._extract_package_from_import(imported_module, result.file_path)
                if imported_package and imported_package != result.package_name:
                    package_relations[result.package_name]["imports"].add(imported_package)
                    package_relations[result.package_name]["import_deps"].add(imported_package)

            # 2. REFERENCES
            for symbol_node in result.symbols:
                call_graph.add_node(symbol_node)
                reference_nodes.append(symbol_node)

            # 4. CLASS HIERARCHIES
            class_hierarchies.update(result.class_hierarchies)

        # Second pass: add all edges (all nodes are now in the graph)
        for result in successful_results:
            # 3. CALL GRAPH (sorted for deterministic edge insertion order)
            for caller_name, callee_name in sorted(result.call_relationships):
                try:
                    call_graph.add_edge(caller_name, callee_name)
                    logger.debug(f"Added edge: {caller_name} -> {callee_name}")
                except ValueError as e:
                    logger.debug(f"Could not add edge: {e}")

            # 4. CLASS HIERARCHIES
            class_hierarchies.update(result.class_hierarchies)

        # Post-processing: Backfill subclasses from superclass relationships.
        # _find_subclasses() only handles Python syntax; deriving subclasses from
        # already-correct superclass lists covers all languages (TS, Go, Java, etc.).
        for child_name, child_info in class_hierarchies.items():
            for parent_name in child_info.get("superclasses", []):
                if parent_name in class_hierarchies:
                    if child_name not in class_hierarchies[parent_name]["subclasses"]:
                        class_hierarchies[parent_name]["subclasses"].append(child_name)

        # Post-processing: Ensure all intermediate parent packages exist.
        # E.g. if "src.models" exists, also register "src" so callers that expect
        # top-level namespace packages can find them.
        parent_packages: dict[str, dict] = {}
        for pkg in list(package_relations.keys()):
            parts = pkg.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                if parent not in package_relations and parent not in parent_packages:
                    parent_packages[parent] = {
                        "imports": set(),
                        "import_deps": set(),
                        "imported_by": set(),
                        "files": [],
                    }
        package_relations.update(parent_packages)

        # Post-processing: Build reverse relationships from import_deps
        for package, info in package_relations.items():
            for imported_pkg in info["import_deps"]:
                if imported_pkg in package_relations:
                    package_relations[imported_pkg]["imported_by"].add(package)

        # Convert sets to sorted lists for deterministic serialization
        for package_info in package_relations.values():
            package_info["imports"] = sorted(package_info["imports"])
            package_info["import_deps"] = sorted(package_info["import_deps"])
            package_info["imported_by"] = sorted(package_info["imported_by"])

        # Per-method edge contribution analysis
        outgoing_set = set(e for r in successful_results for e in r.outgoing_edges)
        incoming_set = set(e for r in successful_results for e in r.incoming_edges)
        body_set = set(e for r in successful_results for e in r.body_edges)
        only_outgoing = len(outgoing_set - incoming_set - body_set)
        only_incoming = len(incoming_set - outgoing_set - body_set)
        only_body = len(body_set - outgoing_set - incoming_set)

        total_hierarchy_time = sum(r.method_timings.get("hierarchy", 0) for r in successful_results)
        total_outgoing_time = sum(r.method_timings.get("outgoing", 0) for r in successful_results)
        total_incoming_time = sum(r.method_timings.get("incoming", 0) for r in successful_results)
        total_body_time = sum(r.method_timings.get("body", 0) for r in successful_results)

        logger.info(
            f"Edge contribution by method: "
            f"outgoing={len(outgoing_set)} (unique_only={only_outgoing}), "
            f"incoming={len(incoming_set)} (unique_only={only_incoming}), "
            f"body={len(body_set)} (unique_only={only_body})"
        )
        logger.info(
            f"Cumulative LSP time (wall-clock sum across files): "
            f"hierarchy={total_hierarchy_time:.1f}s, outgoing={total_outgoing_time:.1f}s, "
            f"incoming={total_incoming_time:.1f}s, body={total_body_time:.1f}s"
        )

        # Language-specific post-processing (e.g. synthesising Java constructor nodes/edges)
        self._post_process_analysis(call_graph, reference_nodes, successful_results)

        logger.info("Unified static analysis complete.")
        logger.info(
            f"Results: {len(reference_nodes)} references, {len(class_hierarchies)} classes, "
            f"{len(package_relations)} packages, {len(call_graph.nodes)} call graph nodes, {len(call_graph.edges)} edges"
        )

        return {
            "call_graph": call_graph,
            "class_hierarchies": class_hierarchies,
            "package_relations": package_relations,
            "references": reference_nodes,
            "source_files": src_files,
            "file_analysis_results": successful_results,
        }

    def _analyze_single_file(self, file_path: Path, all_classes: list[dict]) -> FileAnalysisResult:
        """
        Analyze a single file and return all analysis results.
        Thread-safe method that doesn't modify shared state.
        """
        result = FileAnalysisResult(
            file_path=file_path,
            package_name="",
            imports=[],
            symbols=[],
            function_symbols=[],
            class_symbols=[],
            call_relationships=[],
            class_hierarchies={},
        )

        file_uri = file_path.as_uri()

        try:
            content = file_path.read_text(encoding="utf-8")

            # Populate package name before symbol check so files without symbols
            # still get their correct package name (avoids phantom empty-string packages)
            result.package_name = self._get_package_name(file_path)

            # Get all symbols in this file once
            symbols = self._get_document_symbols(file_uri)
            if not symbols:
                return result

            # 1. PACKAGE RELATIONS - Process imports and package structure
            result.imports = self._extract_imports_from_symbols(symbols, content)

            # 2. REFERENCES - Collect all symbol references
            all_symbols = self._get_all_symbols_recursive(symbols)
            for symbol in all_symbols:
                symbol_kind = symbol.get("kind")
                symbol_name = symbol.get("name", "")

                if symbol_kind not in self.symbol_kinds:
                    continue

                qualified_name = self._create_qualified_name(file_path, symbol_name)
                range_info = symbol.get("range", {})
                start_line = range_info.get("start", {}).get("line", 0)
                end_line = range_info.get("end", {}).get("line", 0)

                node = Node(
                    fully_qualified_name=qualified_name,
                    node_type=symbol_kind,
                    file_path=str(file_path),
                    line_start=start_line,
                    line_end=end_line,
                )
                result.symbols.append(node)

                # Also register the class-qualified alias (e.g. core.base.Dog.create)
                # so that edges resolved via the definition location map can find the node.
                sel_range_start = symbol.get("selectionRange", symbol.get("range", {})).get("start", {})
                sel_line = sel_range_start.get("line")
                if sel_line is not None and file_uri in self._definition_location_map:
                    entry = self._definition_location_map[file_uri].get(sel_line)
                    if entry is not None:
                        class_qualified = entry[0]
                        if class_qualified != qualified_name:
                            alias_node = Node(
                                fully_qualified_name=class_qualified,
                                node_type=symbol_kind,
                                file_path=str(file_path),
                                line_start=start_line,
                                line_end=end_line,
                            )
                            result.symbols.append(alias_node)

            # 3. CALL GRAPH - Process function/method calls using batched LSP requests
            # Instead of sequential request-wait-request per symbol, we fire all requests
            # in each phase first, then collect all responses. This keeps the LSP server's
            # request queue full and eliminates idle gaps between requests.
            result.function_symbols = self._flatten_symbols(symbols)

            # Phase 1: Fire all prepareCallHierarchy requests, then collect responses
            hierarchy_t0 = time.monotonic()
            hierarchy_requests: list[tuple[int, str]] = []  # (req_id, qualified_name)
            for symbol in result.function_symbols:
                pos = symbol["selectionRange"]["start"]
                qualified_name = self._create_qualified_name(file_path, symbol["name"])
                params = {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": pos["line"], "character": pos["character"]},
                }
                req_id = self._send_request("textDocument/prepareCallHierarchy", params)
                hierarchy_requests.append((req_id, qualified_name))

            hierarchy_items_map: dict[str, list[dict]] = {}  # qualified_name -> items
            for req_id, qualified_name in hierarchy_requests:
                try:
                    response = self._wait_for_response(req_id, timeout=20)
                    items = response.get("result") or []
                    if items:
                        hierarchy_items_map[qualified_name] = items
                except TimeoutError:
                    logger.debug(f"Timeout preparing call hierarchy for {qualified_name}")
                except Exception as e:
                    logger.debug(f"Error preparing call hierarchy for {qualified_name}: {e}")
            hierarchy_time = time.monotonic() - hierarchy_t0

            # Phase 2: Fire all outgoing + incoming calls requests, then collect responses
            outgoing_requests: list[tuple[int, str]] = []  # (req_id, qualified_name)
            incoming_requests: list[tuple[int, str]] = []

            for qualified_name, items in hierarchy_items_map.items():
                for item in items:
                    out_id = self._send_request("callHierarchy/outgoingCalls", {"item": item})
                    outgoing_requests.append((out_id, qualified_name))
                    in_id = self._send_request("callHierarchy/incomingCalls", {"item": item})
                    incoming_requests.append((in_id, qualified_name))

            # Collect all outgoing responses
            outgoing_t0 = time.monotonic()
            for req_id, current_qualified_name in outgoing_requests:
                try:
                    response = self._wait_for_response(req_id, timeout=20)
                    outgoing_calls = response.get("result") or []
                    for call in outgoing_calls:
                        callee_item = call["to"]
                        try:
                            callee_uri = callee_item["uri"]
                            if callee_uri.startswith("file://"):
                                callee_path = uri_to_path(callee_uri)
                                callee_name = self._normalize_call_item_name(callee_item)
                                callee_qualified_name = self._create_qualified_name(callee_path, callee_name)
                                edge = (current_qualified_name, callee_qualified_name)
                                result.call_relationships.append(edge)
                                result.outgoing_edges.append(edge)
                                logger.debug(f"Outgoing call: {current_qualified_name} -> {callee_qualified_name}")
                        except Exception as e:
                            logger.debug(f"Error processing outgoing call: {e}")
                except TimeoutError:
                    logger.debug(f"Timeout getting outgoing calls for {current_qualified_name}")
                except Exception as e:
                    logger.debug(f"Error getting outgoing calls for {current_qualified_name}: {e}")
            outgoing_time = time.monotonic() - outgoing_t0

            # Collect all incoming responses
            # required for edge completeness
            incoming_t0 = time.monotonic()
            for req_id, current_qualified_name in incoming_requests:
                try:
                    response = self._wait_for_response(req_id, timeout=20)
                    incoming_calls = response.get("result") or []
                    for call in incoming_calls:
                        caller_item = call["from"]
                        try:
                            caller_uri = caller_item["uri"]
                            if caller_uri.startswith("file://"):
                                caller_path = uri_to_path(caller_uri)
                                caller_name = self._normalize_call_item_name(caller_item)
                                caller_qualified_name = self._create_qualified_name(caller_path, caller_name)
                                edge = (caller_qualified_name, current_qualified_name)
                                result.call_relationships.append(edge)
                                result.incoming_edges.append(edge)
                                logger.debug(f"Incoming call: {caller_qualified_name} -> {current_qualified_name}")
                        except Exception as e:
                            logger.debug(f"Error processing incoming call: {e}")
                except TimeoutError:
                    logger.debug(f"Timeout getting incoming calls for {current_qualified_name}")
                except Exception as e:
                    logger.debug(f"Error getting incoming calls for {current_qualified_name}: {e}")
            incoming_time = time.monotonic() - incoming_t0

            # Body-level calls by finding call positions
            # required for edge completeness
            body_t0 = time.monotonic()
            try:
                # Phase 1: Collect all call positions from all functions (with filtering)
                all_call_requests: list[tuple[str, dict]] = []  # (func_qualified_name, call_pos)
                for func_symbol in result.function_symbols:
                    func_range = func_symbol.get("range", {})
                    func_qualified_name = self._create_qualified_name(file_path, func_symbol["name"])

                    start_line = func_range.get("start", {}).get("line", 0)
                    end_line = func_range.get("end", {}).get("line", 0)
                    call_positions = self._find_call_positions_in_range(content, start_line, end_line)

                    for call_pos in call_positions:
                        all_call_requests.append((func_qualified_name, call_pos))

                # Phase 2: Fire all definition requests in batch
                # Cache: avoid duplicate LSP requests for same position
                definition_cache: dict[tuple[int, int], int | None] = {}  # (line, char) -> request_id
                pending_requests: list[tuple[int, str, dict]] = []  # (req_id, func_name, call_pos)

                for func_qualified_name, call_pos in all_call_requests:
                    cache_key = (call_pos["line"], call_pos["char"])

                    if cache_key in definition_cache:
                        # Already have a pending request for this position
                        req_id = definition_cache[cache_key]
                        if req_id is not None:
                            pending_requests.append((req_id, func_qualified_name, call_pos))
                    else:
                        # Fire new request
                        params = {
                            "textDocument": {"uri": file_uri},
                            "position": {
                                "line": call_pos["line"],
                                "character": call_pos["char"],
                            },
                        }
                        req_id = self._send_request("textDocument/definition", params)
                        definition_cache[cache_key] = req_id
                        pending_requests.append((req_id, func_qualified_name, call_pos))

                # Phase 3: Collect all responses and resolve
                # Group requests by req_id to avoid waiting for same response multiple times
                response_cache: dict[int, list | None] = {}
                for req_id, func_qualified_name, call_pos in pending_requests:
                    if req_id not in response_cache:
                        try:
                            response = self._wait_for_response(req_id, timeout=15)
                            if "error" in response:
                                logger.warning(
                                    "Definition response returned error for req_id=%s file=%s line=%s char=%s func=%s error=%s",
                                    req_id,
                                    file_path,
                                    call_pos["line"],
                                    call_pos["char"],
                                    func_qualified_name,
                                    response.get("error"),
                                )
                                response_cache[req_id] = None
                            else:
                                response_cache[req_id] = response.get("result", [])
                        except TimeoutError as e:
                            logger.warning(
                                "Timed out waiting for definition response: req_id=%s file=%s line=%s char=%s func=%s (%s)",
                                req_id,
                                file_path,
                                call_pos["line"],
                                call_pos["char"],
                                func_qualified_name,
                                e,
                            )
                            response_cache[req_id] = None
                        except Exception:
                            logger.exception(
                                "Error waiting for definition response: req_id=%s file=%s line=%s char=%s func=%s",
                                req_id,
                                file_path,
                                call_pos["line"],
                                call_pos["char"],
                                func_qualified_name,
                            )
                            response_cache[req_id] = None

                    definitions = response_cache.get(req_id)
                    if definitions:
                        resolved_name = self._process_definition_response(definitions, file_path, call_pos)
                        if resolved_name:
                            edge = (func_qualified_name, resolved_name)
                            result.call_relationships.append(edge)
                            result.body_edges.append(edge)
                            logger.debug(f"Body call: {func_qualified_name} -> {resolved_name}")
            except Exception as e:
                logger.debug(f"Error processing body calls for {file_path}: {e}")
            body_time = time.monotonic() - body_t0

            result.method_timings = {
                "hierarchy": hierarchy_time,
                "outgoing": outgoing_time,
                "incoming": incoming_time,
                "body": body_time,
            }

            # 4. CLASS HIERARCHIES - Process class inheritance
            result.class_symbols = self._find_classes_in_symbols(symbols)

            for class_symbol in result.class_symbols:
                qualified_name = self._create_qualified_name(file_path, class_symbol["name"])

                # Get class info
                range_info = class_symbol.get("range", {})
                start_line = range_info.get("start", {}).get("line", 0)
                end_line = range_info.get("end", {}).get("line", 0)

                class_info = {
                    "superclasses": [],
                    "subclasses": [],
                    "file_path": str(file_path),
                    "line_start": start_line,
                    "line_end": end_line,
                }

                # Find inheritance relationships
                superclasses = self._find_superclasses(file_uri, class_symbol, content, file_path)
                class_info["superclasses"] = superclasses

                # Find subclasses by searching references to this class
                subclasses = self._find_subclasses(file_uri, class_symbol, all_classes)
                class_info["subclasses"] = subclasses

                result.class_hierarchies[qualified_name] = class_info

        except Exception as e:
            result.error = str(e)
            logger.error(f"Error processing file {file_path}: {e}")

        # Language-specific per-file post-processing (e.g. Java constructor synthesis)
        self._post_process_file_result(result, file_path)

        return result

    def _post_process_file_result(self, result: FileAnalysisResult, file_path: Path) -> None:
        """Override in subclasses to perform language-specific per-file post-processing.

        Called after all symbols, edges, and hierarchies have been collected for a file.
        May add extra nodes to ``result.symbols`` or extra edges to ``result.call_relationships``.

        Args:
            result: The analysis result for this file (may be modified in-place).
            file_path: The source file path.
        """
        pass

    def close(self):
        """Shuts down the language server gracefully."""
        logger.info("Shutting down langserver...")
        if self._process:
            # LSP shutdown sequence
            shutdown_id = self._send_request("shutdown", {})
            try:
                self._wait_for_response(shutdown_id)
            except TimeoutError:
                logger.warning("Did not receive shutdown confirmation from server.")

            self._send_notification("exit", {})

            # Stop the reader thread and terminate the process
            self._shutdown_flag.set()
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Server did not terminate gracefully. Forcing kill.")
                self._process.kill()
            if self._reader_thread:
                self._reader_thread.join(timeout=2)
            logger.info("Shutdown complete.")

    def _prepare_for_analysis(self):
        """Override in subclasses to perform language-specific preparation before analysis."""
        pass

    def _post_process_analysis(
        self,
        call_graph: CallGraph,
        reference_nodes: list[Node],
        file_results: list[FileAnalysisResult],
    ) -> None:
        """Override in subclasses to perform language-specific post-processing.

        Called after all file analysis results have been assembled into the call graph
        and reference list, but before the final result dict is returned.

        Args:
            call_graph: The assembled call graph (may be modified in-place).
            reference_nodes: All collected reference nodes (may be extended in-place).
            file_results: The per-file analysis results (read-only).
        """
        pass

    def _normalize_call_item_name(self, item: dict) -> str:
        """Return the display name to use for a callHierarchy item.

        Override in subclasses to perform language-specific normalisation.
        The base implementation returns ``item["name"]`` unchanged.

        Args:
            item: A callHierarchy item dict with at least a ``"name"`` key and
                  optionally a ``"kind"`` key (LSP SymbolKind integer).
        """
        return item["name"]

    def _get_all_classes_in_workspace(self) -> list:
        """Get all class symbols in the workspace using workspace/symbol."""
        try:
            params = {"query": ""}
            req_id = self._send_request("workspace/symbol", params)
            # Use longer timeout for workspace-wide symbol search
            response = self._wait_for_response(req_id, timeout=360)

            if "error" in response:
                error_msg = response["error"]
                logger.error(f"workspace/symbol failed: {error_msg}")
                return []

            symbols = response.get("result") or []
            # Filter for class-like symbols (class=5, interface=11, struct=23)
            classes = [s for s in symbols if s.get("kind") in self._CLASS_LIKE_KINDS]
            logger.debug(f"Found {len(classes)} class symbols via workspace/symbol")
            return classes
        except Exception as e:
            logger.error(f"Error getting workspace symbols: {e}")
            return []

    def _find_superclasses(self, file_uri: str, class_symbol: dict, content: str, file_path: Path) -> list:
        """Find superclasses using textDocument/definition and text analysis."""
        superclasses = []

        # Method 1: Use textDocument/definition on class inheritance
        lsp_superclasses = self._find_superclasses_via_definition(file_uri, class_symbol, content)
        superclasses.extend(lsp_superclasses)

        # Method 2: Fallback to text analysis
        if not superclasses:
            text_superclasses = self._extract_superclasses_from_text(file_path, class_symbol["name"], content)
            superclasses.extend(text_superclasses)

        return list(set(superclasses))  # Remove duplicates

    def _find_superclasses_via_definition(self, file_uri: str, class_symbol: dict, content: str) -> list:
        """Use textDocument/definition to find parent classes.

        Handles both:
        - Python syntax: ``class Foo(Bar, Baz):``
        - TypeScript/Java/Go syntax: ``class Foo extends Bar implements Baz``
        """
        superclasses: list[str] = []

        try:
            # Get the class definition line from content
            lines = content.split("\n")
            class_name = class_symbol["name"]
            class_line_idx = None
            class_line = None

            original_line: str | None = None
            for i, line in enumerate(lines):
                stripped = line.strip()
                # Python style: class Foo(Bar):
                if stripped.startswith(f"class {class_name}(") and stripped.endswith(":"):
                    class_line_idx = i
                    class_line = stripped
                    original_line = line
                    break
                # TypeScript/JS style: class Foo extends Bar ... {
                # Also matches `export class Foo extends Bar {`
                if re.search(r"\bclass\s+" + re.escape(class_name) + r"\b", stripped) and (
                    " extends " in stripped or " implements " in stripped or stripped.endswith("{")
                ):
                    class_line_idx = i
                    class_line = stripped
                    original_line = line
                    break

            if class_line_idx is None or class_line is None or original_line is None:
                return superclasses

            # --- TypeScript/Java extends + implements syntax ---
            ts_extends = re.search(r"\bextends\s+([A-Za-z_$][A-Za-z0-9_$<>, ]*?)(?:\s+implements|\s*\{|$)", class_line)
            # Java implements clause: "implements Foo, Bar" after any extends clause
            java_implements = re.search(r"\bimplements\s+([A-Za-z_$][A-Za-z0-9_$<>, ]*?)(?:\s*\{|$)", class_line)
            if ts_extends or java_implements:
                # Build a list of (parent_name, search_start_keyword) tuples
                parents_to_resolve: list[tuple[str, str]] = []
                if ts_extends:
                    for parent_raw in ts_extends.group(1).split(","):
                        parent_name = parent_raw.strip().split("<")[0].strip()
                        if parent_name:
                            parents_to_resolve.append((parent_name, "extends"))
                if java_implements:
                    for parent_raw in java_implements.group(1).split(","):
                        parent_name = parent_raw.strip().split("<")[0].strip()
                        if parent_name:
                            parents_to_resolve.append((parent_name, "implements"))

                for parent_name, keyword in parents_to_resolve:
                    # Use position in the ORIGINAL line (with leading whitespace) for LSP character offset
                    keyword_pos = original_line.find(keyword)
                    parent_pos = original_line.find(parent_name, keyword_pos) if keyword_pos != -1 else -1
                    if parent_pos != -1:
                        definition = self._get_definition_for_position(file_uri, class_line_idx, parent_pos)
                        if definition:
                            for def_item in definition:
                                def_uri = def_item.get("uri", "")
                                if def_uri.startswith("file://"):
                                    def_path = uri_to_path(def_uri)
                                    try:
                                        def_path.relative_to(self.project_path)
                                    except ValueError:
                                        continue
                                    resolved = self._create_qualified_name(def_path, parent_name)
                                    superclasses.append(resolved)
                        else:
                            resolved = self._resolve_class_name(parent_name, file_uri, content)
                            if resolved:
                                superclasses.append(resolved)
                return superclasses

            # --- Python style: class Foo(Bar, Baz): ---
            # Extract parent class names from the line
            start = original_line.find("(") + 1
            end = original_line.rfind(")")
            if 0 < start < end:
                parents_str = original_line[start:end]
                parent_names = [p.strip() for p in parents_str.split(",") if p.strip()]

                # For each parent name, use LSP to get its definition
                for parent_name in parent_names:
                    if parent_name == "object":
                        continue

                    # Find the position of this parent name in the original line
                    parent_start = original_line.find(parent_name, start)
                    if parent_start != -1:
                        # Calculate character position
                        char_pos = parent_start

                        # Use textDocument/definition to resolve the parent class
                        definition = self._get_definition_for_position(file_uri, class_line_idx, char_pos)

                        if definition:
                            for def_item in definition:
                                def_uri = def_item.get("uri", "")
                                if def_uri.startswith("file://"):
                                    def_path = uri_to_path(def_uri)
                                    # Get the symbol at the definition location
                                    def_range = def_item.get("range", {})
                                    def_line = def_range.get("start", {}).get("line", 0)

                                    # Extract class name from definition
                                    try:
                                        def_content = def_path.read_text(encoding="utf-8")
                                        def_lines = def_content.split("\n")
                                        if def_line < len(def_lines):
                                            def_line_text = def_lines[def_line].strip()
                                            if def_line_text.startswith("class "):
                                                class_name_match = (
                                                    def_line_text.split("class ")[1].split("(")[0].split(":")[0].strip()
                                                )
                                                qualified_super = self._create_qualified_name(
                                                    def_path, class_name_match
                                                )
                                                superclasses.append(qualified_super)
                                    except Exception as e:
                                        logger.debug(f"Could not read definition file: {e}")
                        else:
                            # If LSP definition failed, try to resolve manually
                            resolved_name = self._resolve_class_name(parent_name, file_uri, content)
                            if resolved_name:
                                superclasses.append(resolved_name)

        except Exception as e:
            logger.debug(f"Error finding superclasses via definition: {e}")

        return superclasses

    def _get_definition_for_position(self, file_uri: str, line: int, character: int) -> list:
        """Get definition for a specific position."""
        try:
            params = {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            }
            req_id = self._send_request("textDocument/definition", params)
            response = self._wait_for_response(req_id, timeout=15)

            if "error" in response:
                logger.debug(f"Definition request failed: {response['error']}")
                return []

            return response.get("result", [])
        except Exception as e:
            logger.debug(f"Could not get definition: {e}")
            return []

    def _extract_superclasses_from_text(self, file_path: Path, class_name: str, content: str) -> list:
        """Extract superclasses using text analysis as fallback."""
        superclasses = []

        try:
            lines = content.split("\n")

            # Find the class definition line
            for line in lines:
                line = line.strip()
                if line.startswith(f"class {class_name}(") and line.endswith(":"):
                    # Extract parent classes from class definition
                    start = line.find("(") + 1
                    end = line.rfind(")")
                    if start > 0 and end > start:
                        parents_str = line[start:end]
                        parents = [p.strip() for p in parents_str.split(",") if p.strip()]

                        for parent in parents:
                            if parent != "object":
                                # Try to resolve to fully qualified name
                                resolved = self._resolve_class_name(parent, file_path, content)
                                if resolved:
                                    superclasses.append(resolved)
                    break

        except Exception as e:
            logger.debug(f"Could not extract inheritance from text: {e}")

        return superclasses

    def _get_references(self, file_uri: str, line: int, character: int) -> list:
        """Get references for a position using LSP."""
        try:
            params = {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            }
            req_id = self._send_request("textDocument/references", params)
            response = self._wait_for_response(req_id, timeout=20)
            return response.get("result", [])
        except Exception as e:
            logger.debug(f"Could not get references: {e}")
            return []

    def _find_subclasses(self, file_uri: str, class_symbol: dict, all_classes: list) -> list:
        """Find subclasses using textDocument/references."""
        subclasses = []

        try:
            # Get references to this class
            pos = class_symbol["selectionRange"]["start"]
            references = self._get_references(file_uri, pos["line"], pos["character"])

            for ref in references:
                ref_uri = ref.get("uri", "")
                ref_range = ref.get("range", {})
                ref_line = ref_range.get("start", {}).get("line", 0)

                if ref_uri.startswith("file://"):
                    ref_path = uri_to_path(ref_uri)

                    try:
                        # Read the file and check if this reference is in a class inheritance
                        ref_content = ref_path.read_text(encoding="utf-8")
                        ref_lines = ref_content.split("\n")

                        if ref_line < len(ref_lines):
                            ref_line_text = ref_lines[ref_line].strip()

                            # Check if this line is a class definition that inherits from our class
                            if (
                                ref_line_text.startswith("class ")
                                and "(" in ref_line_text
                                and class_symbol["name"] in ref_line_text
                            ):
                                # Extract the subclass name
                                subclass_name = ref_line_text.split("class ")[1].split("(")[0].strip()
                                qualified_subclass = self._create_qualified_name(ref_path, subclass_name)
                                subclasses.append(qualified_subclass)

                    except Exception as e:
                        logger.debug(f"Could not analyze reference file: {e}")

        except Exception as e:
            logger.debug(f"Error finding subclasses: {e}")

        return subclasses

    def _resolve_class_name(self, class_name: str, file_reference, content: str) -> str:
        """Try to resolve a simple class name to a fully qualified name."""
        # If it's already qualified, return as-is
        if "." in class_name:
            return class_name

        # Get file path for context
        if isinstance(file_reference, str) and file_reference.startswith("file://"):
            file_path = uri_to_path(file_reference)
        elif isinstance(file_reference, Path):
            file_path = file_reference
        else:
            return class_name

        # Look for imports in the file that might define this class
        lines = content.split("\n")
        for line in lines[:50]:  # Check imports at top of file
            line = line.strip()
            if f"from " in line and f" import " in line and class_name in line:
                # Try to extract the module
                if f" import {class_name}" in line or f" import {class_name}," in line:
                    module_part = line.split(" import ")[0].replace("from ", "").strip()
                    return f"{module_part}.{class_name}"
            elif f"import " in line and class_name in line:
                # Handle direct imports
                import_part = line.replace("import ", "").strip()
                if "." in import_part and import_part.endswith(class_name):
                    return import_part

        # If we can't resolve it, assume it's in the same package
        file_package = self._get_package_name(file_path)
        if file_package and file_package != "root":
            return f"{file_package}.{class_name}"
        else:
            return class_name

    # LSP symbol kinds that represent class-like entities for hierarchy analysis.
    # Class=5, Interface=11, Struct=23 — covers Python/TS classes as well as
    # Go interfaces and structs which use kinds 11 and 23 respectively.
    _CLASS_LIKE_KINDS = {5, 11, 23}

    def _find_classes_in_symbols(self, symbols: list) -> list:
        """Find all class-like symbols (class, interface, struct) recursively."""
        classes = []
        for symbol in symbols:
            if symbol.get("kind") in self._CLASS_LIKE_KINDS:
                classes.append(symbol)
            if "children" in symbol:
                classes.extend(self._find_classes_in_symbols(symbol["children"]))
        return classes

    def _extract_imports_from_symbols(self, symbols: list, content: str) -> list:
        """Extract import information from symbols and content using LSP only."""
        imports = []

        # Look for module-level symbols that might indicate imports
        for symbol in symbols:
            # Variables at module level might be imports
            if symbol.get("kind") == Node.VARIABLE_TYPE:
                symbol_name = symbol.get("name", "")

                # Use LSP to get definition/references for this symbol
                pos = symbol["selectionRange"]["start"]

                try:
                    # Try to get definition to see if it's an import
                    definition = self._get_definition(pos["line"], pos["character"])
                    if definition:
                        for def_item in definition:
                            uri = def_item.get("uri", "")
                            if uri and not uri.startswith(f"file://{self.project_path}"):
                                # This looks like an external import
                                imports.append(symbol_name)
                except Exception:
                    logger.warning(f"Failed to extract imports from symbol {symbol_name}")
                    continue

        # Also use text-based heuristics for common import patterns
        lines = content.split("\n")
        in_go_import_block = False
        for line in lines[:100]:  # Check first 100 lines where imports usually are
            stripped = line.strip()

            # --- Go-style imports: import "path/to/pkg" or grouped import (...) ---
            # Detect start of a grouped import block
            if stripped == "import (" or stripped.startswith("import ("):
                in_go_import_block = True
                continue
            if in_go_import_block:
                if stripped == ")":
                    in_go_import_block = False
                    continue
                # Lines inside the block are quoted import paths, possibly with aliases
                # e.g.: `"example.com/edgecases/models"` or `m "example.com/edgecases/models"`
                m = re.search(r'"([^"]+)"', stripped)
                if m:
                    imports.append(m.group(1))
                continue
            # Single-line Go import: import "pkg/path"
            if stripped.startswith('import "') and stripped.endswith('"'):
                path = stripped[8:-1]
                imports.append(path)
                continue

            # --- TypeScript/JavaScript-style imports: import ... from 'path' ---
            # Scan every line for `from 'path'` or `from "path"` — this covers:
            #   single-line:  import { A, B } from './path'
            #   multi-line closing: } from "./utils";
            #   type imports: import type { A } from '../path'
            m = re.search(r"""\bfrom\s+['"]([^'"]+)['"]""", stripped)
            if m:
                imports.append(m.group(1))
                continue

            # --- Python-style imports ---
            if stripped.startswith("import ") or stripped.startswith("from "):
                if stripped.startswith("import "):
                    parts = stripped[7:].split()
                    if parts:
                        module = parts[0].split(".")[0]  # Get root module
                        imports.append(module)
                elif stripped.startswith("from ") and " import " in stripped:
                    module_part = stripped[5:].split(" import ")[0].strip()
                    if module_part and not module_part.startswith("."):
                        module = module_part.split(".")[0]  # Get root module
                        imports.append(module)

        return list(set(imports))  # Remove duplicates

    def _get_definition(self, line: int, character: int) -> list:
        """Get definition for a position using LSP."""
        try:
            params = {
                "textDocument": {"uri": "current_file"},  # This would need the actual URI
                "position": {"line": line, "character": character},
            }
            req_id = self._send_request("textDocument/definition", params)
            response = self._wait_for_response(req_id, timeout=15)
            return response.get("result", [])
        except Exception as e:
            logger.debug(f"Could not get definition: {e}")
            return []

    def _get_package_name(self, file_path: Path) -> str:
        """Extract package name from file path.

        Root-level files are named by their stem (e.g. main.py -> 'main')
        rather than being lumped into a single 'root' pseudo-package, which
        would create false circular-dependency cycles.
        """
        try:
            rel_path = file_path.relative_to(self.project_path)
            package_parts = rel_path.parent.parts
            if package_parts and package_parts[0] != ".":
                return ".".join(package_parts)
            else:
                return rel_path.stem
        except ValueError:
            return "external"

    def _extract_package_from_import(self, module_name: str, importing_file: Path | None = None) -> str:
        """Extract the relevant package name from an import path.

        Handles:
        - Python-style dotted names: "core.services" → "core"
        - Go-style slash-separated module paths: "example.com/edgecases/models" → "models"
        - TypeScript/JS relative paths: "./models" resolved via importing_file context
          e.g. importing_file=src/index.ts, module_name="./models" → "src.models"
        - TypeScript/JS relative paths with dots: "../utils" resolved similarly
        """
        if not module_name:
            return ""

        # TypeScript/JavaScript relative import: starts with "./" or "../"
        if module_name.startswith(".") and importing_file is not None:
            try:
                # Resolve the import path relative to the importing file's directory.
                # The resolved path may point to a directory (index.ts) or file without extension.
                resolved = (importing_file.parent / module_name).resolve()
                # Get relative path from project root and convert to dotted package name.
                rel = resolved.relative_to(self.project_path)
                return str(rel).replace(os.sep, ".")
            except Exception:
                return ""

        # Go-style: slash-separated module path — return the last segment
        if "/" in module_name:
            return module_name.rstrip("/").rsplit("/", 1)[-1]

        # Python/JS-style: dotted name — return the top-level package
        parts = module_name.split(".")
        return parts[0] if parts else ""

    def _get_all_symbols_recursive(self, symbols: list) -> list:
        """Recursively collect all symbols from a hierarchical symbol list."""
        all_symbols = []
        for symbol in symbols:
            all_symbols.append(symbol)
            if "children" in symbol:
                all_symbols.extend(self._get_all_symbols_recursive(symbol["children"]))
        return all_symbols

    def filter_src_files(self, src_files, spec=None):
        """Filter source files using the centralized RepoIgnoreManager."""
        # First use the centralized manager
        filtered = [f for f in src_files if not self.ignore_manager.should_ignore(f)]

        # Then apply the additional spec if provided (backward compatibility for tests)
        if spec is not None:
            filtered = [f for f in filtered if not spec.match_file(str(f))]

        return filtered

    def get_exclude_dirs(self) -> pathspec.PathSpec:
        """Backward compatibility for tests."""
        return self.ignore_manager.spec

    def _analyze_specific_files(self, file_paths: set[Path]) -> dict:
        """
        Analyze only the specified files and return analysis results.

        This method performs static analysis on a specific set of files,
        which is used during incremental analysis to reanalyze only
        changed files.

        Args:
            file_paths: Set of file paths to analyze

        Returns:
            Dictionary containing analysis results for the specified files:
            - 'call_graph': CallGraph object with function call relationships
            - 'class_hierarchies': dict mapping class names to inheritance info
            - 'package_relations': dict mapping package names to dependencies
            - 'references': list of Node objects for all symbols
            - 'source_files': list of analyzed file paths
        """
        logger.info(f"Analyzing {len(file_paths)} specific files for incremental update")

        # Filter the file paths to only include files that would normally be analyzed
        src_files = self._get_source_files()
        filtered_src_files = self.filter_src_files(src_files)

        # Only analyze files that are both in file_paths and in the normal source file set
        files_to_analyze = []
        for file_path in file_paths:
            if file_path in filtered_src_files:
                files_to_analyze.append(file_path)
                logger.debug(f"Will reanalyze: {file_path}")

        if not files_to_analyze:
            logger.info("No relevant source files to reanalyze")
            return {
                "call_graph": CallGraph(),
                "class_hierarchies": {},
                "package_relations": {},
                "references": [],
                "source_files": [],
            }

        logger.info(f"Reanalyzing {len(files_to_analyze)} files")

        # Perform analysis on specific files without reassigning methods
        logger.debug("Starting targeted analysis of changed files")
        analysis_result = self.build_static_analysis(source_files_override=files_to_analyze)

        logger.info(
            f"Targeted analysis complete: {len(analysis_result.get('references', []))} references, "
            f"{len(analysis_result.get('class_hierarchies', {}))} classes, "
            f"{len(analysis_result.get('package_relations', {}))} packages"
        )

        # Filter results to only include data from analyzed files
        src_files_set = set(str(f) for f in files_to_analyze)
        analysis_result["references"] = [
            ref for ref in analysis_result.get("references", []) if ref.file_path in src_files_set
        ]
        analysis_result["source_files"] = [
            f for f in analysis_result.get("source_files", []) if str(f) in src_files_set
        ]

        # Filter call graph nodes and edges to only include analyzed files
        cg = analysis_result.get("call_graph", CallGraph())
        nodes_to_keep = {name: node for name, node in cg.nodes.items() if node.file_path in src_files_set}
        edges_to_keep = []
        for edge in cg.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()
            if src_name in nodes_to_keep and dst_name in nodes_to_keep:
                edges_to_keep.append(edge)

        # Rebuild call graph with filtered data
        filtered_cg = CallGraph()
        for node in nodes_to_keep.values():
            filtered_cg.add_node(node)
        for edge in edges_to_keep:
            try:
                filtered_cg.add_edge(edge.get_source(), edge.get_destination())
            except ValueError:
                pass  # Skip if nodes don't exist
        analysis_result["call_graph"] = filtered_cg

        # Filter class hierarchies to only include analyzed files
        filtered_classes = {}
        for class_name, class_info in analysis_result.get("class_hierarchies", {}).items():
            if class_info.get("file_path") in src_files_set:
                filtered_classes[class_name] = class_info
        analysis_result["class_hierarchies"] = filtered_classes

        # Filter package relations to only include analyzed files
        filtered_packages = {}
        for pkg_name, pkg_info in analysis_result.get("package_relations", {}).items():
            pkg_files = pkg_info.get("files", [])
            remaining_files = [f for f in pkg_files if f in src_files_set]
            if remaining_files:
                filtered_packages[pkg_name] = pkg_info.copy()
                filtered_packages[pkg_name]["files"] = remaining_files
        analysis_result["package_relations"] = filtered_packages

        logger.info(
            f"Filtered to {len(analysis_result['references'])} references, {len(analysis_result['class_hierarchies'])} classes from analyzed files"
        )

        return analysis_result
