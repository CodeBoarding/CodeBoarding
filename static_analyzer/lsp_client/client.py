import subprocess
import json
import threading
import time
import os
import sys
import logging
from tqdm import tqdm
from pathlib import Path
from typing import List
import pathspec
import argparse

from static_analyzer.graph import CallGraph, Node

# Configure logging
logger = logging.getLogger(__name__)


class PyrightClient:
    """
    A Python client for the Pyright Language Server that communicates over stdio.
    This client is designed to build a call graph of a Python project.
    """

    def __init__(self, project_path: str, server_start_params: List[str], language_suffix: str = 'py'):
        """
        Initializes the client and starts the pyright-langserver process.
        """
        self.project_path = Path(project_path).resolve()
        if not self.project_path.is_dir():
            raise ValueError(f"Project path '{project_path}' does not exist or is not a directory.")

        self.server_start_params = server_start_params
        self._process = None
        self._reader_thread = None
        self._shutdown_flag = threading.Event()
        self.language_suffix = language_suffix

        self._message_id = 1
        self._responses = {}
        self._notifications = []
        self._lock = threading.Lock()

        # Initialize CallGraph
        self.call_graph = CallGraph()

    def start(self):
        """Starts the language server process and the message reader thread."""
        logger.info("Starting pyright-langserver...")
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
        logger.info("Initializing connection...")
        params = {
            'processId': os.getpid(),
            'rootUri': self.project_path.as_uri(),
            'capabilities': {
                'textDocument': {
                    'callHierarchy': {'dynamicRegistration': True},
                    'documentSymbol': {'hierarchicalDocumentSymbolSupport': True}
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

    def _get_document_symbols(self, file_uri: str):
        """Fetches all document symbols (functions, classes, etc.) for a file."""
        params = {'textDocument': {'uri': file_uri}}
        req_id = self._send_request('textDocument/documentSymbol', params)
        response = self._wait_for_response(req_id)
        return response.get('result', [])

    def _prepare_call_hierarchy(self, file_uri: str, line: int, character: int):
        """Prepares a call hierarchy at a specific location."""
        params = {
            'textDocument': {'uri': file_uri},
            'position': {'line': line, 'character': character}
        }
        req_id = self._send_request('textDocument/prepareCallHierarchy', params)
        response = self._wait_for_response(req_id)
        return response.get('result')

    def _get_incoming_calls(self, item: dict):
        """Gets incoming calls for a call hierarchy item."""
        req_id = self._send_request('callHierarchy/incomingCalls', {'item': item})
        response = self._wait_for_response(req_id)
        return response.get('result', [])

    def _flatten_symbols(self, symbols: list):
        """Recursively flattens a list of hierarchical symbols."""
        flat_list = []
        for symbol in symbols:
            # SymbolKind: 6 for function, 7 for method in LSP 3.17
            if symbol['kind'] in [6, 7, 12]:  # 12 is Function
                flat_list.append(symbol)
            if 'children' in symbol:
                flat_list.extend(self._flatten_symbols(symbol['children']))
        return flat_list

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
        py_files = list(self.project_path.rglob(f'*.{self.language_suffix}'))
        spec = self.get_exclude_dirs()
        py_files = self.filter_python_files(py_files, spec)
        total_files = len(py_files)
        logger.info(f"Found {total_files} Python files. Analyzing...")

        if not py_files:
            logger.warning("No Python files found in the project.")
            return self.call_graph

        for i, file_path in tqdm(enumerate(py_files), desc="Processing files", total=total_files):
            file_uri = file_path.as_uri()

            # 1. Notify the server that the file is open
            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': 'python', 'version': 1, 'text': content}
                })
            except Exception as e:
                logger.error(f"Could not read file {file_path}: {e}")
                continue

            # 2. Get all functions/methods in the file
            symbols = self._get_document_symbols(file_uri)
            if not symbols:
                continue

            # 3. Create nodes for all functions in this file
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

    def filter_python_files(self, python_files, spec):
        # Return files that do NOT match any of the ignore patterns AND do not have "test" in their path
        filtered_files = []
        for file in python_files:
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
        logger.info("Shutting down pyright-langserver...")
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Build a call graph for a Python project using pyright's LSP.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("project_dir", help="The root directory of the Python project.")
    parser.add_argument(
        "-o", "--output",
        help="Path to save the output JSON file. If not provided, prints to console."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging to show all LSP JSON-RPC communication."
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    client = None
    try:
        # Resolve the project directory path
        project_path = Path(args.project_dir).resolve()

        # Instantiate and start the client
        client = PyrightClient(str(project_path), ['pyright-langserver', '--stdio'])
        client.start()

        # Build the graph
        call_graph = client.build_call_graph()
        print(call_graph)
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if client:
            client.close()
