import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import pathspec
from tqdm import tqdm

from static_analyzer.graph import CallGraph, Node
from static_analyzer.scanner import ProgrammingLanguage

# Configure logging
logger = logging.getLogger(__name__)


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

    def start(self):
        """Starts the language server process and the message reader thread."""
        logger.info(f"Starting server {' '.join(self.server_start_params)}...")
        self._process = subprocess.Popen(
            self.server_start_params,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
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
            'jsonrpc': '2.0',
            'id': message_id,
            'method': method,
            'params': params,
        }

        body = json.dumps(request)
        message = f"Content-Length: {len(body)}\r\n\r\n{body}"

        self._process.stdin.write(message.encode('utf-8'))
        self._process.stdin.flush()

        return message_id

    def _send_notification(self, method: str, params: dict):
        """Sends a JSON-RPC notification to the server."""
        notification = {
            'jsonrpc': '2.0',
            'method': method,
            'params': params,
        }
        body = json.dumps(notification)
        message = f"Content-Length: {len(body)}\r\n\r\n{body}"

        self._process.stdin.write(message.encode('utf-8'))
        self._process.stdin.flush()

    def _read_messages(self):
        """
        Runs in a separate thread to read and process messages from the server's stdout.
        """
        while not self._shutdown_flag.is_set():
            try:
                line = self._process.stdout.readline().decode('utf-8')
                if not line or not line.startswith('Content-Length'):
                    continue

                content_length = int(line.split(':')[1].strip())
                self._process.stdout.readline()  # Read the blank line

                body = self._process.stdout.read(content_length).decode('utf-8')
                response = json.loads(body)

                if 'id' in response:
                    with self._lock:
                        self._responses[response['id']] = response
                else:  # It's a notification from the server
                    with self._lock:
                        self._notifications.append(response)

            except (IOError, ValueError) as e:
                if not self._shutdown_flag.is_set():
                    logger.error(f"Error reading from server: {e}")
                break

    def _wait_for_response(self, message_id: int, timeout: int = 10):
        """Waits for a response with a specific message ID to arrive."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._lock:
                if message_id in self._responses:
                    return self._responses.pop(message_id)
            time.sleep(0.01)
        raise TimeoutError(f"Timed out waiting for response to message {message_id}")

    def _initialize(self):
        """Performs the LSP initialization handshake."""
        logger.info(f"Initializing connection for {self.language_id}...")
        params = {
            'processId': os.getpid(),
            'rootUri': self.project_path.as_uri(),
            'capabilities': {
                'textDocument': {
                    'callHierarchy': {'dynamicRegistration': True},
                    'documentSymbol': {'hierarchicalDocumentSymbolSupport': True},
                    'typeHierarchy': {'dynamicRegistration': True},
                    'references': {'dynamicRegistration': True},
                    'semanticTokens': {'dynamicRegistration': True}
                }
            },
            'workspace': {
                'applyEdit': True,
                'workspaceEdit': {'documentChanges': True}
            }
        }
        init_id = self._send_request('initialize', params)
        response = self._wait_for_response(init_id, timeout=20)

        if 'error' in response:
            raise RuntimeError(f"Initialization failed: {response['error']}")

        logger.info("Initialization successful.")
        self._send_notification('initialized', {})

    def _get_document_symbols(self, file_uri: str) -> list:
        """Fetches all document symbols (functions, classes, etc.) for a file."""
        params = {'textDocument': {'uri': file_uri}}
        req_id = self._send_request('textDocument/documentSymbol', params)
        logger.info(f"Requesting document symbols for {file_uri} with ID {req_id}")
        response = self._wait_for_response(req_id)
        return response.get('result', [])

    def _prepare_call_hierarchy(self, file_uri: str, line: int, character: int) -> list:
        """Prepares a call hierarchy at a specific location."""
        params = {
            'textDocument': {'uri': file_uri},
            'position': {'line': line, 'character': character}
        }
        req_id = self._send_request('textDocument/prepareCallHierarchy', params)
        response = self._wait_for_response(req_id)
        return response.get('result', [])

    def _get_incoming_calls(self, item: dict) -> list:
        """Gets incoming calls for a call hierarchy item."""
        req_id = self._send_request('callHierarchy/incomingCalls', {'item': item})
        response = self._wait_for_response(req_id)
        return response.get('result', [])

    def _flatten_symbols(self, symbols: list):
        """Recursively flattens a list of hierarchical symbols."""
        flat_list = []
        for symbol in symbols:
            # SymbolKind: 6 for method, 7 for property in LSP 3.17
            if symbol['kind'] in [6, 7, 12]:  # 12 is Function
                flat_list.append(symbol)
            if 'children' in symbol:
                flat_list.extend(self._flatten_symbols(symbol['children']))
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

    def build_call_graph(self) -> CallGraph:
        """
        Builds the call graph for the entire project.

        Returns:
            A CallGraph object containing all function call relationships.
        """
        src_files = self._get_source_files()
        spec = self.get_exclude_dirs()
        src_files = self.filter_src_files(src_files, spec)
        total_files = len(src_files)

        logger.info(f"Found {total_files} source files. Analyzing...")

        if not src_files:
            logger.warning("No source files found in the project.")
            return self.call_graph

        for i, file_path in tqdm(enumerate(src_files), desc="[CallGraph] Processing files", total=total_files):
            file_uri = file_path.as_uri()

            # 1. Notify the server that the file is open
            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': self.language_id, 'version': 1, 'text': content}
                })
            except Exception as e:
                logger.error(f"Could not read file {file_path}: {e}")
                continue

            # 2. Get all functions/methods in the file
            symbols = self._get_document_symbols(file_uri)
            if not symbols:
                continue

            # 3. Create nodes for all functions and methods in this file
            function_symbols = self._flatten_symbols(symbols)
            if not function_symbols:
                logger.debug(f"No functions found in {file_path}. Skipping.")
                continue

            # Create nodes for all functions in this file
            for symbol in function_symbols:
                qualified_name = self._create_qualified_name(file_path, symbol['name'])
                range_info = symbol.get('range', {})
                start_line = range_info.get('start', {}).get('line', 0)
                end_line = range_info.get('end', {}).get('line', 0)

                node = Node(
                    fully_qualified_name=qualified_name,
                    file_path=str(file_path),
                    line_start=start_line,
                    line_end=end_line
                )
                self.call_graph.add_node(node)
                logger.debug(f"Added node: {qualified_name}")

            # 4. Iterate through symbols to find incoming calls
            for symbol in function_symbols:
                # Use the start of the selection range for the symbol's position
                pos = symbol['selectionRange']['start']
                callee_qualified_name = self._create_qualified_name(file_path, symbol['name'])

                # Prepare the call hierarchy at the function's position
                hierarchy_items = self._prepare_call_hierarchy(file_uri, pos['line'], pos['character'])
                if not hierarchy_items:
                    logger.warning(f"No call hierarchy items found for {callee_qualified_name}.")
                    continue

                # Get incoming calls to this function
                for item in hierarchy_items:
                    incoming_calls = self._get_incoming_calls(item)
                    if not incoming_calls:
                        logger.warning(f"No incoming calls found for {callee_qualified_name}.")
                        continue
                    for call in incoming_calls:
                        caller_item = call['from']
                        caller_path = Path(caller_item['uri'].replace('file://', ''))
                        caller_qualified_name = self._create_qualified_name(caller_path, caller_item['name'])

                        # Create node for caller if it doesn't exist (external function)
                        if caller_qualified_name not in self.call_graph.nodes:
                            caller_node = Node(
                                fully_qualified_name=caller_qualified_name,
                                file_path=str(caller_path),
                                line_start=0,
                                line_end=0
                            )
                            self.call_graph.add_node(caller_node)
                            logger.debug(f"Added external node: {caller_qualified_name}")

                        # Add edge from caller to callee
                        try:
                            self.call_graph.add_edge(caller_qualified_name, callee_qualified_name)
                            logger.info(f"Added edge: {caller_qualified_name} -> {callee_qualified_name}")
                        except ValueError as e:
                            logger.debug(f"Could not add edge: {e}")

            # Close the document
            self._send_notification('textDocument/didClose', {'textDocument': {'uri': file_uri}})

        logger.info("Call graph construction complete.")
        return self.call_graph

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

    def close(self):
        """Shuts down the language server gracefully."""
        logger.info("Shutting down langserver...")
        if self._process:
            # LSP shutdown sequence
            shutdown_id = self._send_request('shutdown', {})
            try:
                self._wait_for_response(shutdown_id, timeout=5)
            except TimeoutError:
                logger.warning("Did not receive shutdown confirmation from server.")

            self._send_notification('exit', {})

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

    def build_class_hierarchies(self) -> dict:
        """
        Collects all class hierarchies in the project using core LSP methods.

        Returns:
            A dictionary mapping class names to their inheritance information.
        """
        logger.info("Collecting class hierarchies using core LSP methods...")
        class_hierarchies = {}

        # Allow subclasses to perform pre-analysis setup
        self._prepare_for_analysis()

        # First, get all classes in the workspace using workspace/symbol
        all_classes = self._get_all_classes_in_workspace()
        logger.info(f"Found {len(all_classes)} classes in workspace")

        src_files = self._get_source_files()
        spec = self.get_exclude_dirs()
        src_files = self.filter_src_files(src_files, spec)

        for file_path in tqdm(src_files, desc="[Class Hierarchy] Analyzing class hierarchies"):
            file_uri = file_path.as_uri()

            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': self.language_id, 'version': 1, 'text': content}
                })

                # Get document symbols to find classes in this file
                symbols = self._get_document_symbols(file_uri)
                class_symbols = self._find_classes_in_symbols(symbols)

                for class_symbol in class_symbols:
                    qualified_name = self._create_qualified_name(file_path, class_symbol['name'])

                    # Get class info
                    range_info = class_symbol.get('range', {})
                    start_line = range_info.get('start', {}).get('line', 0)
                    end_line = range_info.get('end', {}).get('line', 0)

                    class_info = {
                        "superclasses": [],
                        "subclasses": [],
                        "file_path": str(file_path),
                        "line_start": start_line,
                        "line_end": end_line
                    }

                    # Find inheritance relationships using multiple methods
                    superclasses = self._find_superclasses(file_uri, class_symbol, content, file_path)
                    class_info["superclasses"] = superclasses
                    for superclass in superclasses:
                        if superclass not in class_hierarchies:
                            class_hierarchies[superclass] = {"superclasses": [], "subclasses": [], "file_path": "",
                                                             "line_start": 0, "line_end": 0}
                        class_hierarchies[superclass]["subclasses"].append(qualified_name)

                    # Find subclasses by searching references to this class
                    subclasses = self._find_subclasses(file_uri, class_symbol, all_classes)
                    class_info["subclasses"] = subclasses

                    class_hierarchies[qualified_name] = class_info
                    for subclass in subclasses:
                        if subclass not in class_hierarchies:
                            class_hierarchies[subclass] = {"superclasses": [], "subclasses": [], "file_path": "",
                                                           "line_start": 0, "line_end": 0}
                        class_hierarchies[subclass]["superclasses"].append(qualified_name)
                    logger.debug(
                        f"Found class: {qualified_name} with {len(superclasses)} supers, {len(subclasses)} subs")

                self._send_notification('textDocument/didClose', {'textDocument': {'uri': file_uri}})

            except Exception as e:
                logger.error(f"Error processing class hierarchies in {file_path}: {e}")
                continue

        logger.info(f"Found {len(class_hierarchies)} classes with hierarchy information")
        return class_hierarchies

    def _prepare_for_analysis(self):
        """Override in subclasses to perform language-specific preparation before analysis."""
        pass

    def _get_all_classes_in_workspace(self) -> list:
        """Get all class symbols in the workspace using workspace/symbol."""
        try:
            params = {'query': ''}
            req_id = self._send_request('workspace/symbol', params)
            response = self._wait_for_response(req_id, timeout=30)

            if 'error' in response:
                error_msg = response['error']
                logger.error(f"workspace/symbol failed: {error_msg}")
                return []

            symbols = response.get('result', [])
            # Filter for class symbols (kind 5)
            classes = [s for s in symbols if s.get('kind') == 5]
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
            text_superclasses = self._extract_superclasses_from_text(file_path, class_symbol['name'], content)
            superclasses.extend(text_superclasses)

        return list(set(superclasses))  # Remove duplicates

    def _find_superclasses_via_definition(self, file_uri: str, class_symbol: dict, content: str) -> list:
        """Use textDocument/definition to find parent classes."""
        superclasses = []

        try:
            # Get the class definition line from content
            lines = content.split('\n')
            class_name = class_symbol['name']
            class_line_idx = None

            # Find the line with class definition
            for i, line in enumerate(lines):
                if line.strip().startswith(f'class {class_name}(') and line.strip().endswith(':'):
                    class_line_idx = i
                    break

            if class_line_idx is None:
                return superclasses

            class_line = lines[class_line_idx].strip()

            # Extract parent class names from the line
            start = class_line.find('(') + 1
            end = class_line.rfind(')')
            if start > 0 and end > start:
                parents_str = class_line[start:end]
                parent_names = [p.strip() for p in parents_str.split(',') if p.strip()]

                # For each parent name, use LSP to get its definition
                for parent_name in parent_names:
                    if parent_name == 'object':
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
                                def_uri = def_item.get('uri', '')
                                if def_uri.startswith('file://'):
                                    def_path = Path(def_uri.replace('file://', ''))
                                    # Get the symbol at the definition location
                                    def_range = def_item.get('range', {})
                                    def_line = def_range.get('start', {}).get('line', 0)

                                    # Extract class name from definition
                                    try:
                                        def_content = def_path.read_text(encoding='utf-8')
                                        def_lines = def_content.split('\n')
                                        if def_line < len(def_lines):
                                            def_line_text = def_lines[def_line].strip()
                                            if def_line_text.startswith('class '):
                                                class_name_match = \
                                                    def_line_text.split('class ')[1].split('(')[0].split(':')[0].strip()
                                                qualified_super = self._create_qualified_name(def_path,
                                                                                              class_name_match)
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
                'textDocument': {'uri': file_uri},
                'position': {'line': line, 'character': character}
            }
            req_id = self._send_request('textDocument/definition', params)
            response = self._wait_for_response(req_id)

            if 'error' in response:
                logger.debug(f"Definition request failed: {response['error']}")
                return []

            return response.get('result', [])
        except Exception as e:
            logger.debug(f"Could not get definition: {e}")
            return []

    def _extract_superclasses_from_text(self, file_path: Path, class_name: str, content: str) -> list:
        """Extract superclasses using text analysis as fallback."""
        superclasses = []

        try:
            lines = content.split('\n')

            # Find the class definition line
            for line in lines:
                line = line.strip()
                if line.startswith(f'class {class_name}(') and line.endswith(':'):
                    # Extract parent classes from class definition
                    start = line.find('(') + 1
                    end = line.rfind(')')
                    if start > 0 and end > start:
                        parents_str = line[start:end]
                        parents = [p.strip() for p in parents_str.split(',') if p.strip()]

                        for parent in parents:
                            if parent != 'object':
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
            pos = class_symbol['selectionRange']['start']
            references = self._get_references(file_uri, pos['line'], pos['character'])

            for ref in references:
                ref_uri = ref.get('uri', '')
                ref_range = ref.get('range', {})
                ref_line = ref_range.get('start', {}).get('line', 0)

                if ref_uri.startswith('file://'):
                    ref_path = Path(ref_uri.replace('file://', ''))

                    try:
                        # Read the file and check if this reference is in a class inheritance
                        ref_content = ref_path.read_text(encoding='utf-8')
                        ref_lines = ref_content.split('\n')

                        if ref_line < len(ref_lines):
                            ref_line_text = ref_lines[ref_line].strip()

                            # Check if this line is a class definition that inherits from our class
                            if ref_line_text.startswith('class ') and '(' in ref_line_text and class_symbol[
                                'name'] in ref_line_text:
                                # Extract the subclass name
                                subclass_name = ref_line_text.split('class ')[1].split('(')[0].strip()
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
        if '.' in class_name:
            return class_name

        # Get file path for context
        if isinstance(file_reference, str) and file_reference.startswith('file://'):
            file_path = Path(file_reference.replace('file://', ''))
        elif isinstance(file_reference, Path):
            file_path = file_reference
        else:
            return class_name

        # Look for imports in the file that might define this class
        lines = content.split('\n')
        for line in lines[:50]:  # Check imports at top of file
            line = line.strip()
            if f'from ' in line and f' import ' in line and class_name in line:
                # Try to extract the module
                if f' import {class_name}' in line or f' import {class_name},' in line:
                    module_part = line.split(' import ')[0].replace('from ', '').strip()
                    return f"{module_part}.{class_name}"
            elif f'import ' in line and class_name in line:
                # Handle direct imports
                import_part = line.replace('import ', '').strip()
                if '.' in import_part and import_part.endswith(class_name):
                    return import_part

        # If we can't resolve it, assume it's in the same package
        file_package = self._get_package_name(file_path)
        if file_package and file_package != 'root':
            return f"{file_package}.{class_name}"
        else:
            return class_name

    def build_package_relations(self) -> dict:
        """
        Collects package interaction relationships using pure LSP by analyzing symbols and references.
        
        Returns:
            A dictionary mapping package names to their dependencies:
            {
                "package.name": {
                    "imports": ["other.package1", "other.package2"],
                    "imported_by": ["dependent.package1"],
                    "files": ["/path/to/file1.py", "/path/to/file2.py"]
                }
            }
        """
        logger.info("Collecting package relations...")
        package_relations = {}

        src_files = self._get_source_files()
        spec = self.get_exclude_dirs()
        src_files = self.filter_src_files(src_files, spec)

        # First pass: collect all symbols and build package structure
        for file_path in tqdm(src_files, desc="[Package Relationships] Analyzing package structure"):
            file_uri = file_path.as_uri()

            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': self.language_id, 'version': 1, 'text': content}
                })

                # Determine package for this file
                file_package = self._get_package_name(file_path)

                if file_package not in package_relations:
                    package_relations[file_package] = {
                        "imports": set(),
                        "imported_by": set(),
                        "files": []
                    }

                package_relations[file_package]["files"].append(str(file_path))

                # Get document symbols to find import-like symbols
                symbols = self._get_document_symbols(file_uri)
                imports = self._extract_imports_from_symbols(symbols, content)

                # Add imports to this package's imports
                for imported_module in imports:
                    imported_package = self._extract_package_from_import(imported_module)
                    if imported_package and imported_package != file_package:
                        package_relations[file_package]["imports"].add(imported_package)

                logger.debug(f"File {file_path} belongs to package {file_package}, found {len(imports)} imports")

                self._send_notification('textDocument/didClose', {'textDocument': {'uri': file_uri}})

            except Exception as e:
                logger.error(f"Error analyzing package structure in {file_path}: {e}")
                continue

        # Second pass: use LSP references to find cross-package dependencies
        for file_path in tqdm(src_files, desc="[Package Relationships] Enhancing with LSP references"):
            file_uri = file_path.as_uri()

            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': self.language_id, 'version': 1, 'text': content}
                })

                # Get symbols and find references to external packages
                symbols = self._get_document_symbols(file_uri)
                external_refs = self._find_external_references(file_uri, symbols)

                file_package = self._get_package_name(file_path)

                for ref in external_refs:
                    ref_package = self._extract_package_from_reference(ref)
                    if ref_package and ref_package != file_package:
                        if file_package in package_relations:
                            package_relations[file_package]["imports"].add(ref_package)

                self._send_notification('textDocument/didClose', {'textDocument': {'uri': file_uri}})

            except Exception as e:
                logger.error(f"Error analyzing references in {file_path}: {e}")
                continue

        # Build reverse relationships (imported_by)
        for package, info in package_relations.items():
            for imported_pkg in info["imports"]:
                if imported_pkg in package_relations:
                    package_relations[imported_pkg]["imported_by"].add(package)

        # Convert sets to lists for serialization
        for package_info in package_relations.values():
            package_info["imports"] = list(package_info["imports"])
            package_info["imported_by"] = list(package_info["imported_by"])

        logger.info(f"Found {len(package_relations)} packages with relationship information")
        return package_relations

    def _find_classes_in_symbols(self, symbols: list) -> list:
        """Find all class symbols recursively."""
        classes = []
        for symbol in symbols:
            if symbol.get('kind') == 5:  # Class symbol kind
                classes.append(symbol)
            if 'children' in symbol:
                classes.extend(self._find_classes_in_symbols(symbol['children']))
        return classes

    def _extract_imports_from_symbols(self, symbols: list, content: str) -> list:
        """Extract import information from symbols and content using LSP only."""
        imports = []

        # Look for module-level symbols that might indicate imports
        for symbol in symbols:
            # Variables at module level might be imports
            if symbol.get('kind') == 13:  # Variable symbol kind
                symbol_name = symbol.get('name', '')

                # Use LSP to get definition/references for this symbol
                pos = symbol['selectionRange']['start']

                try:
                    # Try to get definition to see if it's an import
                    definition = self._get_definition(pos['line'], pos['character'])
                    if definition:
                        for def_item in definition:
                            uri = def_item.get('uri', '')
                            if uri and not uri.startswith(f'file://{self.project_path}'):
                                # This looks like an external import
                                imports.append(symbol_name)
                except Exception:
                    logger.warning(f"Failed to extract imports from symbol {symbol_name}")
                    continue

        # Also use text-based heuristics for common import patterns
        lines = content.split('\n')
        for line in lines[:50]:  # Only check first 50 lines where imports usually are
            line = line.strip()
            if line.startswith('import ') or line.startswith('from '):
                # Extract module name using simple parsing
                if line.startswith('import '):
                    parts = line[7:].split()
                    if parts:
                        module = parts[0].split('.')[0]  # Get root module
                        imports.append(module)
                elif line.startswith('from ') and ' import ' in line:
                    module_part = line[5:].split(' import ')[0].strip()
                    if module_part and not module_part.startswith('.'):
                        module = module_part.split('.')[0]  # Get root module
                        imports.append(module)

        return list(set(imports))  # Remove duplicates

    def _get_definition(self, line: int, character: int) -> list:
        """Get definition for a position using LSP."""
        try:
            params = {
                'textDocument': {'uri': 'current_file'},  # This would need the actual URI
                'position': {'line': line, 'character': character}
            }
            req_id = self._send_request('textDocument/definition', params)
            response = self._wait_for_response(req_id)
            return response.get('result', [])
        except Exception as e:
            logger.debug(f"Could not get definition: {e}")
            return []

    def _get_package_name(self, file_path: Path) -> str:
        """Extract package name from file path."""
        try:
            rel_path = file_path.relative_to(self.project_path)
            # Remove file name and convert to package notation
            package_parts = rel_path.parent.parts
            if package_parts and package_parts[0] != '.':
                return '.'.join(package_parts)
            else:
                # Root level file
                return 'root'
        except ValueError:
            return 'external'

    @staticmethod
    def _extract_package_from_import(module_name: str) -> str:
        """Extract top-level package from an import module name."""
        if not module_name or module_name.startswith('.'):
            return ""

        # Get the top-level package
        parts = module_name.split('.')
        return parts[0] if parts else None

    def _find_external_references(self, file_uri: str, symbols: list) -> list:
        """Find references to external symbols using LSP."""
        external_refs = []
        try:
            # This is a simplified approach - in practice, you'd need to iterate through
            # identifiers in the file and use textDocument/references
            for symbol in symbols:
                if symbol.get('kind') in [12, 6]:  # Functions and methods
                    pos = symbol['selectionRange']['start']
                    refs = self._get_references(file_uri, pos['line'], pos['character'])
                    external_refs.extend(refs)
        except Exception as e:
            logger.debug(f"Error finding external references: {e}")
        return external_refs

    def _get_references(self, file_uri: str, line: int, character: int) -> list:
        """Get references for a position using LSP."""
        try:
            params = {
                'textDocument': {'uri': file_uri},
                'position': {'line': line, 'character': character},
                'context': {'includeDeclaration': True}
            }
            req_id = self._send_request('textDocument/references', params)
            response = self._wait_for_response(req_id)
            return response.get('result', [])
        except Exception as e:
            logger.debug(f"Could not get references: {e}")
            return []

    def _extract_package_from_reference(self, reference: dict) -> str:
        """Extract package name from a reference location."""
        try:
            uri = reference.get('uri', '')
            if uri.startswith('file://'):
                file_path = Path(uri.replace('file://', ''))
                return self._get_package_name(file_path)
        except Exception:
            pass
        return ""

    def build_references(self) -> list:
        """
        Builds a list of reference nodes for all methods/functions and classes in the project.
        
        Returns a list of Nodes
        """
        logger.info("Building references for all symbols...")
        reference_nodes = []

        src_files = self._get_source_files()
        spec = self.get_exclude_dirs()
        src_files = self.filter_src_files(src_files, spec)

        # Track processed symbols to avoid duplicates
        processed_symbols = set()

        for file_path in tqdm(src_files, desc="[References collection] Collecting symbol references"):
            file_uri = file_path.as_uri()

            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': self.language_id, 'version': 1, 'text': content}
                })

                # Get all symbols in this file
                symbols = self._get_document_symbols(file_uri)

                # Process functions, methods, and classes
                all_symbols = self._get_all_symbols_recursive(symbols)

                for symbol in all_symbols:
                    symbol_kind = symbol.get('kind')
                    symbol_name = symbol.get('name', '')

                    # Filter for functions (12), methods (6), and classes (5)
                    if symbol_kind not in [5, 6, 12]:
                        continue

                    # Create qualified name
                    qualified_name = self._create_qualified_name(file_path, symbol_name)

                    # Skip if already processed
                    if qualified_name in processed_symbols:
                        continue

                    processed_symbols.add(qualified_name)

                    # Get definition location
                    range_info = symbol.get('range', {})
                    start_line = range_info.get('start', {}).get('line', 0)
                    end_line = range_info.get('end', {}).get('line', 0)

                    node = Node(fully_qualified_name=qualified_name,
                                file_path=str(file_path),
                                line_start=start_line,
                                line_end=end_line)

                    reference_nodes.append(node)
                    logger.debug(f"Added symbol {qualified_name}")

                self._send_notification('textDocument/didClose', {'textDocument': {'uri': file_uri}})

            except Exception as e:
                logger.error(f"Error collecting references in {file_path}: {e}")
                continue

        logger.info(f"Built {len(reference_nodes)} reference nodes")
        return reference_nodes

    def _get_all_symbols_recursive(self, symbols: list) -> list:
        """Recursively collect all symbols from a hierarchical symbol list."""
        all_symbols = []
        for symbol in symbols:
            all_symbols.append(symbol)
            if 'children' in symbol:
                all_symbols.extend(self._get_all_symbols_recursive(symbol['children']))
        return all_symbols
