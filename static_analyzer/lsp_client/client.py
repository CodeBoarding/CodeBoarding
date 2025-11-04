import json
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pathspec
from tqdm import tqdm

from codeboarding.static_analyzer.graph import CallGraph, Node
from codeboarding.static_analyzer.scanner import ProgrammingLanguage

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class FileAnalysisResult:
    """Container for single file analysis results"""

    file_path: Path
    package_name: str
    imports: List[str]
    symbols: List[Node]
    function_symbols: List[dict]
    class_symbols: List[dict]
    call_relationships: List[tuple]  # (caller_qualified_name, callee_qualified_name)
    class_hierarchies: Dict[str, dict]
    external_references: List[dict]
    error: str = None


class LSPClient:
    """
    Language server protocol client for interacting with langservers
    """

    def __init__(self, project_path: Path, language: ProgrammingLanguage):
        """
        Initializes the client and starts the langserver process.
        """
        self.project_path = project_path
        if not self.project_path.is_dir():
            raise ValueError(f"Project path '{project_path}' does not exist or is not a directory.")

        self.language = language
        self.server_start_params = language.get_server_parameters()
        self._process = None
        self._reader_thread = None
        self._shutdown_flag = threading.Event()
        self.language_suffix_pattern = language.get_suffix_pattern()
        self.language_id = language.get_language_id()

        self._message_id = 1
        self._responses = {}
        self._notifications = []
        self._lock = threading.Lock()

        # Initialize CallGraph
        self.call_graph = CallGraph()
        self.symbol_kinds = list(range(1, 27))  # all types from the LSP for now

    def start(self):
        """Starts the language server process and the message reader thread."""
        logger.info(f"Starting server {' '.join(self.server_start_params)}...")
        self._process = subprocess.Popen(
            self.server_start_params, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        self._reader_thread = threading.Thread(target=self._read_messages)
        self._reader_thread.daemon = True
        self._reader_thread.start()
        self._initialize()

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

        self._process.stdin.write(message.encode("utf-8"))
        self._process.stdin.flush()

    def _read_messages(self):
        """
        Runs in a separate thread to read and process messages from the server's stdout.
        """
        while not self._shutdown_flag.is_set():
            try:
                line = self._process.stdout.readline().decode("utf-8")
                if not line or not line.startswith("Content-Length"):
                    continue

                content_length = int(line.split(":")[1].strip())
                self._process.stdout.readline()  # Read the blank line

                body = self._process.stdout.read(content_length).decode("utf-8")
                response = json.loads(body)

                if "id" in response:
                    with self._lock:
                        self._responses[response["id"]] = response
                else:  # It's a notification from the server
                    with self._lock:
                        self._notifications.append(response)

            except (IOError, ValueError) as e:
                if not self._shutdown_flag.is_set():
                    logger.error(f"Error reading from server: {e}")
                break

    def _wait_for_response(self, message_id: int, timeout: int = 360):
        """Waits for a response with a specific message ID to arrive."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._lock:
                if message_id in self._responses:
                    return self._responses.pop(message_id)
            time.sleep(0.01)
        raise TimeoutError(f"Timed out waiting for response to message {message_id}, after {timeout} seconds.")

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
                }
            },
            "workspace": {"applyEdit": True, "workspaceEdit": {"documentChanges": True}},
        }
        init_id = self._send_request("initialize", params)
        response = self._wait_for_response(init_id)

        if "error" in response:
            raise RuntimeError(f"Initialization failed: {response['error']}")

        logger.info("Initialization successful.")
        self._send_notification("initialized", {})

    def _get_document_symbols(self, file_uri: str) -> list:
        """Fetches all document symbols (functions, classes, etc.) for a file."""
        params = {"textDocument": {"uri": file_uri}}
        req_id = self._send_request("textDocument/documentSymbol", params)
        logger.info(f"Requesting document symbols for {file_uri} with ID {req_id}")
        response = self._wait_for_response(req_id)
        return response.get("result", [])

    def _prepare_call_hierarchy(self, file_uri: str, line: int, character: int) -> list:
        """Prepares a call hierarchy at a specific location."""
        params = {"textDocument": {"uri": file_uri}, "position": {"line": line, "character": character}}
        req_id = self._send_request("textDocument/prepareCallHierarchy", params)
        response = self._wait_for_response(req_id)
        return response.get("result", [])

    def _get_incoming_calls(self, item: dict) -> list:
        """Gets incoming calls for a call hierarchy item."""
        req_id = self._send_request("callHierarchy/incomingCalls", {"item": item})
        response = self._wait_for_response(req_id)
        return response.get("result", [])

    def _get_outgoing_calls(self, item: dict) -> list:
        """Gets outgoing calls for a call hierarchy item."""
        req_id = self._send_request("callHierarchy/outgoingCalls", {"item": item})
        response = self._wait_for_response(req_id)
        return response.get("result", [])

    def _find_call_positions_in_range(self, content: str, start_line: int, end_line: int) -> List[dict]:
        """
        Find positions of function/method calls within a line range.
        A call is identified by finding '(' and working backwards to get the identifier.
        Returns list of: {'line': int, 'char': int, 'name': str}
        """
        lines = content.split("\n")
        call_positions = []

        for line_idx in range(start_line + 1, min(end_line + 1, len(lines))):
            line = lines[line_idx]

            # Find all '(' in the line
            for char_idx in range(len(line)):
                if line[char_idx] != "(":
                    continue

                # Work backwards to find the identifier
                # Skip whitespace before '('
                name_end = char_idx - 1
                while name_end >= 0 and line[name_end] in " \t":
                    name_end -= 1

                if name_end < 0:
                    continue

                # Extract identifier (alphanumeric + underscore)
                name_start = name_end
                while name_start >= 0 and (line[name_start].isalnum() or line[name_start] == "_"):
                    name_start -= 1
                name_start += 1

                if name_start > name_end:
                    continue

                identifier = line[name_start : name_end + 1]

                # Skip keywords and invalid identifiers
                if not identifier or identifier[0].isdigit():
                    continue

                keywords = {
                    "if",
                    "for",
                    "while",
                    "def",
                    "class",
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
                if identifier in keywords:
                    continue

                call_positions.append({"line": line_idx, "char": name_start, "name": identifier})

        return call_positions

    def _resolve_call_position(self, file_uri: str, file_path: Path, call_pos: dict) -> Optional[str]:
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

            def_path = Path(def_uri.replace("file://", ""))

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
        return src_files

    def _create_qualified_name(self, file_path: Path, symbol_name: str) -> str:
        """Create a fully qualified name for a symbol."""
        try:
            rel_path = file_path.relative_to(self.project_path)
            module_path = str(rel_path.with_suffix("")).replace("/", ".")
            return f"{module_path}.{symbol_name}"
        except ValueError:
            # File is outside project root
            return f"{file_path.name}.{symbol_name}"

    def build_static_analysis(self) -> dict:
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
        class_hierarchies = {}
        package_relations = {}
        reference_nodes = []

        # Get source files and apply filters
        src_files = self._get_source_files()
        spec = self.get_exclude_dirs()
        src_files = self.filter_src_files(src_files, spec)
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

        # Get all classes in workspace for hierarchy analysis
        all_classes = self._get_all_classes_in_workspace()
        logger.info(f"Found {len(all_classes)} classes in workspace")

        max_workers = max(1, os.cpu_count() - 1)  # Use the number of cores but reserve one
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
                        result = future.result()
                        if result.error:
                            logger.error(f"Error processing {file_path}: {result.error}")
                        else:
                            successful_results.append(result)
                    except Exception as e:
                        logger.error(f"Exception processing {file_path}: {e}")
                    finally:
                        pbar.update(1)

        logger.info(f"Successfully processed {len(successful_results)} files")

        for result in successful_results:
            # 1. PACKAGE RELATIONS
            if result.package_name not in package_relations:
                package_relations[result.package_name] = {"imports": set(), "imported_by": set(), "files": []}
            package_relations[result.package_name]["files"].append(str(result.file_path))

            for imported_module in result.imports:
                imported_package = self._extract_package_from_import(imported_module)
                if imported_package and imported_package != result.package_name:
                    package_relations[result.package_name]["imports"].add(imported_package)

            for ref in result.external_references:
                ref_package = self._extract_package_from_reference(ref)
                if ref_package and ref_package != result.package_name:
                    package_relations[result.package_name]["imports"].add(ref_package)

            # 2. REFERENCES
            for symbol_node in result.symbols:
                call_graph.add_node(symbol_node)
                reference_nodes.append(symbol_node)

            # 3. CALL GRAPH
            for caller_name, callee_name in result.call_relationships:
                try:
                    call_graph.add_edge(caller_name, callee_name)
                    logger.debug(f"Added edge: {caller_name} -> {callee_name}")
                except ValueError as e:
                    logger.debug(f"Could not add edge: {e}")

            # 4. CLASS HIERARCHIES
            class_hierarchies.update(result.class_hierarchies)

        # Post-processing: Build reverse relationships for package relations
        for package, info in package_relations.items():
            for imported_pkg in info["imports"]:
                if imported_pkg in package_relations:
                    package_relations[imported_pkg]["imported_by"].add(package)

        # Convert sets to lists for serialization
        for package_info in package_relations.values():
            package_info["imports"] = list(package_info["imports"])
            package_info["imported_by"] = list(package_info["imported_by"])

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
        }

    def _analyze_single_file(self, file_path: Path, all_classes: List[dict]) -> FileAnalysisResult:
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
            external_references=[],
        )

        file_uri = file_path.as_uri()

        try:
            content = file_path.read_text(encoding="utf-8")

            # Thread-safe LSP communication (each thread gets its own connection context)
            self._send_notification(
                "textDocument/didOpen",
                {"textDocument": {"uri": file_uri, "languageId": self.language_id, "version": 1, "text": content}},
            )

            # Get all symbols in this file once
            symbols = self._get_document_symbols(file_uri)
            if not symbols:
                self._send_notification("textDocument/didClose", {"textDocument": {"uri": file_uri}})
                return result

            # 1. PACKAGE RELATIONS - Process imports and package structure
            result.package_name = self._get_package_name(file_path)
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

            # 3. CALL GRAPH - Process function/method calls
            result.function_symbols = self._flatten_symbols(symbols)

            # Find call relationships - we need BOTH incoming AND outgoing calls
            for symbol in result.function_symbols:
                pos = symbol["selectionRange"]["start"]
                current_qualified_name = self._create_qualified_name(file_path, symbol["name"])

                # Prepare the call hierarchy at the function's position
                hierarchy_items = self._prepare_call_hierarchy(file_uri, pos["line"], pos["character"])
                if not hierarchy_items:
                    continue

                for item in hierarchy_items:
                    # METHOD 1: Get OUTGOING calls (what this function calls)
                    # This is the PRIMARY method - captures all calls made by this function
                    outgoing_calls = self._get_outgoing_calls(item)
                    if outgoing_calls:
                        for call in outgoing_calls:
                            callee_item = call["to"]
                            try:
                                callee_uri = callee_item["uri"]
                                if callee_uri.startswith("file://"):
                                    callee_path = Path(callee_uri.replace("file://", ""))
                                    callee_qualified_name = self._create_qualified_name(
                                        callee_path, callee_item["name"]
                                    )

                                    # Add edge: current_function -> called_function
                                    result.call_relationships.append((current_qualified_name, callee_qualified_name))
                                    logger.debug(f"Outgoing call: {current_qualified_name} -> {callee_qualified_name}")
                            except Exception as e:
                                logger.debug(f"Error processing outgoing call: {e}")

                    # METHOD 2: Get INCOMING calls (who calls this function)
                    # This is SUPPLEMENTARY - helps catch calls we might have missed
                    incoming_calls = self._get_incoming_calls(item)
                    if incoming_calls:
                        for call in incoming_calls:
                            caller_item = call["from"]
                            try:
                                caller_uri = caller_item["uri"]
                                if caller_uri.startswith("file://"):
                                    caller_path = Path(caller_uri.replace("file://", ""))
                                    caller_qualified_name = self._create_qualified_name(
                                        caller_path, caller_item["name"]
                                    )

                                    # Add edge: calling_function -> current_function
                                    result.call_relationships.append((caller_qualified_name, current_qualified_name))
                                    logger.debug(f"Incoming call: {caller_qualified_name} -> {current_qualified_name}")
                            except Exception as e:
                                logger.debug(f"Error processing incoming call: {e}")

            # METHOD 3: Body-level calls by finding call positions
            try:
                for func_symbol in result.function_symbols:
                    func_range = func_symbol.get("range", {})
                    func_qualified_name = self._create_qualified_name(file_path, func_symbol["name"])

                    # Find all call positions in this function's body
                    start_line = func_range.get("start", {}).get("line", 0)
                    end_line = func_range.get("end", {}).get("line", 0)
                    call_positions = self._find_call_positions_in_range(content, start_line, end_line)

                    # Resolve each call
                    for call_pos in call_positions:
                        resolved_name = self._resolve_call_position(file_uri, file_path, call_pos)
                        if resolved_name:
                            result.call_relationships.append((func_qualified_name, resolved_name))
                            logger.debug(f"Body call: {func_qualified_name} -> {resolved_name}")
            except Exception as e:
                logger.debug(f"Error processing body calls for {file_path}: {e}")

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

            # 5. EXTERNAL REFERENCES
            result.external_references = self._find_external_references(file_uri, symbols)

            self._send_notification("textDocument/didClose", {"textDocument": {"uri": file_uri}})

        except Exception as e:
            result.error = str(e)
            logger.error(f"Error processing file {file_path}: {e}")

        return result

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
            self._reader_thread.join(timeout=2)
            logger.info("Shutdown complete.")

    def _prepare_for_analysis(self):
        """Override in subclasses to perform language-specific preparation before analysis."""
        pass

    def _get_all_classes_in_workspace(self) -> list:
        """Get all class symbols in the workspace using workspace/symbol."""
        try:
            params = {"query": ""}
            req_id = self._send_request("workspace/symbol", params)
            response = self._wait_for_response(req_id)

            if "error" in response:
                error_msg = response["error"]
                logger.error(f"workspace/symbol failed: {error_msg}")
                return []

            symbols = response.get("result", [])
            # Filter for class symbols (kind 5)
            classes = [s for s in symbols if s.get("kind") == 5]
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
        """Use textDocument/definition to find parent classes."""
        superclasses = []

        try:
            # Get the class definition line from content
            lines = content.split("\n")
            class_name = class_symbol["name"]
            class_line_idx = None

            # Find the line with class definition
            for i, line in enumerate(lines):
                if line.strip().startswith(f"class {class_name}(") and line.strip().endswith(":"):
                    class_line_idx = i
                    break

            if class_line_idx is None:
                return superclasses

            class_line = lines[class_line_idx].strip()

            # Extract parent class names from the line
            start = class_line.find("(") + 1
            end = class_line.rfind(")")
            if 0 < start < end:
                parents_str = class_line[start:end]
                parent_names = [p.strip() for p in parents_str.split(",") if p.strip()]

                # For each parent name, use LSP to get its definition
                for parent_name in parent_names:
                    if parent_name == "object":
                        continue

                    # Find the position of this parent name in the class line
                    parent_start = class_line.find(parent_name, start)
                    if parent_start != -1:
                        # Calculate character position
                        char_pos = parent_start

                        # Use textDocument/definition to resolve the parent class
                        definition = self._get_definition_for_position(file_uri, class_line_idx, char_pos)

                        if definition:
                            for def_item in definition:
                                def_uri = def_item.get("uri", "")
                                if def_uri.startswith("file://"):
                                    def_path = Path(def_uri.replace("file://", ""))
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
            params = {"textDocument": {"uri": file_uri}, "position": {"line": line, "character": character}}
            req_id = self._send_request("textDocument/definition", params)
            response = self._wait_for_response(req_id)

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
                    ref_path = Path(ref_uri.replace("file://", ""))

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
            file_path = Path(file_reference.replace("file://", ""))
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

    def _find_classes_in_symbols(self, symbols: list) -> list:
        """Find all class symbols recursively."""
        classes = []
        for symbol in symbols:
            if symbol.get("kind") == 5:  # Class symbol kind
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
            if symbol.get("kind") == 13:  # Variable symbol kind
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
        for line in lines[:50]:  # Only check first 50 lines where imports usually are
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                # Extract module name using simple parsing
                if line.startswith("import "):
                    parts = line[7:].split()
                    if parts:
                        module = parts[0].split(".")[0]  # Get root module
                        imports.append(module)
                elif line.startswith("from ") and " import " in line:
                    module_part = line[5:].split(" import ")[0].strip()
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
            response = self._wait_for_response(req_id)
            return response.get("result", [])
        except Exception as e:
            logger.debug(f"Could not get definition: {e}")
            return []

    def _get_package_name(self, file_path: Path) -> str:
        """Extract package name from file path."""
        try:
            rel_path = file_path.relative_to(self.project_path)
            # Remove file name and convert to package notation
            package_parts = rel_path.parent.parts
            if package_parts and package_parts[0] != ".":
                return ".".join(package_parts)
            else:
                # Root level file
                return "root"
        except ValueError:
            return "external"

    @staticmethod
    def _extract_package_from_import(module_name: str) -> str:
        """Extract top-level package from an import module name."""
        if not module_name or module_name.startswith("."):
            return ""

        # Get the top-level package
        parts = module_name.split(".")
        return parts[0] if parts else None

    def _find_external_references(self, file_uri: str, symbols: list) -> list:
        """Find references to external symbols using LSP."""
        external_refs = []
        try:
            # This is a simplified approach - in practice, you'd need to iterate through
            # identifiers in the file and use textDocument/references
            for symbol in symbols:
                if symbol.get("kind") in [12, 6]:  # Functions and methods
                    pos = symbol["selectionRange"]["start"]
                    refs = self._get_references(file_uri, pos["line"], pos["character"])
                    external_refs.extend(refs)
        except Exception as e:
            logger.debug(f"Error finding external references: {e}")
        return external_refs

    def _get_references(self, file_uri: str, line: int, character: int) -> list:
        """Get references for a position using LSP."""
        try:
            params = {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            }
            req_id = self._send_request("textDocument/references", params)
            response = self._wait_for_response(req_id)
            return response.get("result", [])
        except Exception as e:
            logger.debug(f"Could not get references: {e}")
            return []

    def _extract_package_from_reference(self, reference: dict) -> str:
        """Extract package name from a reference location."""
        try:
            uri = reference.get("uri", "")
            if uri.startswith("file://"):
                file_path = Path(uri.replace("file://", ""))
                return self._get_package_name(file_path)
        except Exception:
            pass
        return ""

    def _get_all_symbols_recursive(self, symbols: list) -> list:
        """Recursively collect all symbols from a hierarchical symbol list."""
        all_symbols = []
        for symbol in symbols:
            all_symbols.append(symbol)
            if "children" in symbol:
                all_symbols.extend(self._get_all_symbols_recursive(symbol["children"]))
        return all_symbols

    def filter_src_files(self, src_files, spec):
        # Return files that do NOT match any of the ignore patterns AND do not have "test" in their path
        filtered_files = []
        for file in src_files:
            rel_path = file.relative_to(self.project_path)
            # Skip if matches gitignore patterns
            if spec.match_file(rel_path):
                continue
            # Skip if "test" is in the path (case-insensitive)
            if "test" in str(rel_path).lower():
                logger.debug(f"Skipping test file: {rel_path}")
                continue
            # Skip if file is in a hidden directory (starts with a dot_
            if any(part.startswith(".") for part in rel_path.parts):
                logger.debug(f"Skipping hidden directory file: {rel_path}")
                continue
            filtered_files.append(file)
        return filtered_files

    def get_exclude_dirs(self):
        gitignore_path = self.project_path / ".gitignore"
        if not gitignore_path.exists():
            return pathspec.PathSpec.from_lines("gitwildmatch", [])

        with gitignore_path.open() as f:
            lines = f.readlines()

        # Compile .gitignore patterns using pathspec
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        return spec
