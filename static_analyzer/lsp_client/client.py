import subprocess
import json
import threading
import time
import os
import sys
from pathlib import Path
from collections import defaultdict
import pathspec
import argparse

# Set to True to see all JSON-RPC communication
VERBOSE_LOGGING = False


class PyrightClient:
    """
    A Python client for the Pyright Language Server that communicates over stdio.
    This client is designed to build a call graph of a Python project.
    """

    def __init__(self, project_path: str):
        """
        Initializes the client and starts the pyright-langserver process.
        """
        self.project_path = Path(project_path).resolve()
        if not self.project_path.is_dir():
            raise ValueError(f"Project path '{project_path}' does not exist or is not a directory.")

        self._process = None
        self._reader_thread = None
        self._shutdown_flag = threading.Event()

        self._message_id = 1
        self._responses = {}
        self._notifications = []
        self._lock = threading.Lock()

    def start(self):
        """Starts the language server process and the message reader thread."""
        print("Starting pyright-langserver...")
        self._process = subprocess.Popen(
            ['pyright-langserver', '--stdio'],
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

        if VERBOSE_LOGGING:
            print(f"--> Sending request: {json.dumps(request, indent=2)}")

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

        if VERBOSE_LOGGING:
            print(f"--> Sending notification: {json.dumps(notification, indent=2)}")

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

                if VERBOSE_LOGGING:
                    print(f"<-- Received message: {json.dumps(response, indent=2)}")

                if 'id' in response:
                    with self._lock:
                        self._responses[response['id']] = response
                else:  # It's a notification from the server
                    with self._lock:
                        self._notifications.append(response)

            except (IOError, ValueError) as e:
                if not self._shutdown_flag.is_set():
                    print(f"Error reading from server: {e}")
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
        print("Initializing connection...")
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

        print("Initialization successful.")
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

    def _get_outgoing_calls(self, item: dict):
        """Gets outgoing calls for a call hierarchy item."""
        req_id = self._send_request('callHierarchy/outgoingCalls', {'item': item})
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

    def build_call_graph(self) -> dict:
        """
        Builds the call graph for the entire project.

        Returns:
            A dictionary where keys are caller functions and values are lists of
            functions they call. Both are represented as formatted strings.
        """
        call_graph = defaultdict(set)
        py_files = list(self.project_path.rglob('*.py'))
        spec = self.get_exclude_dirs()
        py_files = self.filter_python_files(py_files, spec)
        total_files = len(py_files)
        print(f"Found {total_files} Python files. Analyzing...")

        if not py_files:
            print("No Python files found in the project.")
        for i, file_path in enumerate(py_files):
            print(f"[{i + 1}/{total_files}] Processing: {file_path.relative_to(self.project_path)}")
            file_uri = file_path.as_uri()

            # 1. Notify the server that the file is open
            try:
                content = file_path.read_text(encoding='utf-8')
                self._send_notification('textDocument/didOpen', {
                    'textDocument': {'uri': file_uri, 'languageId': 'python', 'version': 1, 'text': content}
                })
            except Exception as e:
                print(f"  - Could not read file {file_path}: {e}")
                continue

            # 2. Get all functions/methods in the file
            symbols = self._get_document_symbols(file_uri)
            if not symbols:
                continue

            # 3. Iterate through symbols to find outgoing calls
            function_symbols = self._flatten_symbols(symbols)
            if not function_symbols:
                print(f"  - No functions found in {file_path}. Skipping.")
            for symbol in function_symbols:
                # Use the start of the selection range for the symbol's position
                pos = symbol['selectionRange']['start']
                caller_name = f"{file_path.relative_to(self.project_path)}::{symbol['name']}"

                # Prepare the call hierarchy at the function's position
                hierarchy_items = self._prepare_call_hierarchy(file_uri, pos['line'], pos['character'])
                if not hierarchy_items:
                    continue

                # Get outgoing calls from this function
                if not hierarchy_items:
                    print(f"  - No call hierarchy items found for {caller_name}. Skipping.")
                for item in hierarchy_items:
                    outgoing_calls = self._get_outgoing_calls(item)
                    if not outgoing_calls:
                        print(f"  - No outgoing calls found for {caller_name}.")
                        continue
                    for call in outgoing_calls:
                        callee_item = call['to']
                        callee_path = Path(callee_item['uri'].replace('file://', ''))

                        # Make path relative if it's within the project
                        try:
                            rel_path = callee_path.relative_to(self.project_path)
                        except ValueError:
                            rel_path = callee_path

                        callee_name = f"{rel_path}::{callee_item['name']}"
                        call_graph[caller_name].add(callee_name)
                        print(f"  - {caller_name} calls {callee_name}")
        print("Call graph construction complete.")
        # Convert sets to lists for easier JSON serialization/printing
        return {k: list(v) for k, v in call_graph.items()}

    def filter_python_files(self, python_files, spec):
        # Return files that do NOT match any of the ignore patterns
        return [file for file in python_files if not spec.match_file(file.relative_to(self.project_path))]

    def get_exclude_dirs(self):
        gitignore_path = self.project_path / ".gitignore"
        if not gitignore_path.exists():
            return []

        with gitignore_path.open() as f:
            lines = f.readlines()

        # Compile .gitignore patterns using pathspec
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        return spec

    def close(self):
        """Shuts down the language server gracefully."""
        print("\nShutting down pyright-langserver...")
        if self._process:
            # LSP shutdown sequence
            shutdown_id = self._send_request('shutdown', {})
            try:
                self._wait_for_response(shutdown_id, timeout=5)
            except TimeoutError:
                print("Warning: Did not receive shutdown confirmation from server.")

            self._send_notification('exit', {})

            # Stop the reader thread and terminate the process
            self._shutdown_flag.set()
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("Warning: Server did not terminate gracefully. Forcing kill.")
                self._process.kill()
            self._reader_thread.join(timeout=2)
            print("Shutdown complete.")


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

    if args.verbose:
        VERBOSE_LOGGING = True

    client = None
    try:
        # Resolve the project directory path
        project_path = Path(args.project_dir).resolve()

        # Instantiate and start the client
        client = PyrightClient(str(project_path))
        client.start()

        # Build the graph
        graph = client.build_call_graph()

        # Output the result
        output_json = json.dumps(graph, indent=4)
        if args.output:
            output_file = Path(args.output)
            output_file.write_text(output_json)
            print(f"\nCall graph saved to {output_file}")
        else:
            print("\n--- Call Graph ---")
            print(output_json)
            print("------------------")

    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if client:
            client.close()
