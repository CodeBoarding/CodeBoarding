import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open

from static_analyzer.graph import CallGraph, Node
from static_analyzer.lsp_client.client import LSPClient, FileAnalysisResult, uri_to_path
from static_analyzer.scanner import ProgrammingLanguage


class TestUriToPath(unittest.TestCase):
    def test_uri_to_path_unix(self):
        # Test converting Unix-style file URI to path
        file_uri = "file:///home/user/project/file.py"
        result = uri_to_path(file_uri)

        # Result should be a Path object
        self.assertIsInstance(result, Path)
        self.assertIn("file.py", str(result))

    def test_uri_to_path_windows(self):
        # Test converting Windows-style file URI to path
        file_uri = "file:///C:/Users/test/project/file.py"
        result = uri_to_path(file_uri)

        self.assertIsInstance(result, Path)
        self.assertIn("file.py", str(result))

    def test_uri_to_path_with_encoding(self):
        # Test URI with encoded characters
        file_uri = "file:///home/user/my%20project/file.py"
        result = uri_to_path(file_uri)

        # Should decode %20 to space
        self.assertIn("my project", str(result))


class TestFileAnalysisResult(unittest.TestCase):
    def test_file_analysis_result_creation(self):
        # Test creating a FileAnalysisResult
        file_path = Path("/test/file.py")
        result = FileAnalysisResult(
            file_path=file_path,
            package_name="test.package",
            imports=["os", "sys"],
            symbols=[],
            function_symbols=[],
            class_symbols=[],
            call_relationships=[],
            class_hierarchies={},
            external_references=[],
        )

        self.assertEqual(result.file_path, file_path)
        self.assertEqual(result.package_name, "test.package")
        self.assertEqual(len(result.imports), 2)
        self.assertIsNone(result.error)

    def test_file_analysis_result_with_error(self):
        # Test FileAnalysisResult with error
        result = FileAnalysisResult(
            file_path=Path("/test/file.py"),
            package_name="test",
            imports=[],
            symbols=[],
            function_symbols=[],
            class_symbols=[],
            call_relationships=[],
            class_hierarchies={},
            external_references=[],
            error="Test error message",
        )

        self.assertEqual(result.error, "Test error message")


class TestLSPClient(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = Path(self.temp_dir)

        # Create mock language
        self.mock_language = Mock(spec=ProgrammingLanguage)
        self.mock_language.get_server_parameters.return_value = ["python-lsp-server"]
        self.mock_language.get_suffix_pattern.return_value = ["*.py"]
        self.mock_language.get_language_id.return_value = "python"

    def tearDown(self):
        # Clean up temporary directory
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_invalid_project_path(self):
        # Test initialization with invalid path
        invalid_path = Path("/nonexistent/path")

        with self.assertRaises(ValueError) as context:
            LSPClient(invalid_path, self.mock_language)

        self.assertIn("does not exist", str(context.exception))

    def test_init_valid_project_path(self):
        # Test initialization with valid path
        client = LSPClient(self.project_path, self.mock_language)

        self.assertEqual(client.project_path, self.project_path)
        self.assertEqual(client.language, self.mock_language)
        self.assertIsInstance(client.call_graph, CallGraph)
        self.assertEqual(client._message_id, 1)
        self.assertEqual(len(client._responses), 0)

    @patch("static_analyzer.lsp_client.client.threading.Thread")
    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_start(self, mock_popen, mock_thread):
        # Test starting the LSP server
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_process.stderr = Mock()
        mock_popen.return_value = mock_process

        mock_thread_instance = Mock()
        mock_thread.return_value = mock_thread_instance

        client = LSPClient(self.project_path, self.mock_language)

        # Mock _initialize to avoid actual initialization
        client._initialize = Mock()  # type: ignore[method-assign]  # type: ignore[method-assign]

        client.start()

        # Verify process was started
        mock_popen.assert_called()
        # Verify reader thread was created and started
        mock_thread_instance.start.assert_called()
        # Verify initialization was called
        client._initialize.assert_called_once()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_send_request(self, mock_popen):
        # Test sending a request
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        # Manually set the process since we're not calling start()
        client._process = mock_process

        method = "test/method"
        params = {"key": "value"}

        message_id = client._send_request(method, params)

        self.assertEqual(message_id, 1)
        self.assertEqual(client._message_id, 2)

        # Verify message was written to stdin
        mock_process.stdin.write.assert_called_once()
        mock_process.stdin.flush.assert_called_once()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_send_notification(self, mock_popen):
        # Test sending a notification
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        # Manually set the process since we're not calling start()
        client._process = mock_process

        method = "test/notification"
        params = {"key": "value"}

        client._send_notification(method, params)

        # Message ID should not change for notifications
        self.assertEqual(client._message_id, 1)

        # Verify message was written
        mock_process.stdin.write.assert_called_once()
        mock_process.stdin.flush.assert_called_once()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_wait_for_response_success(self, mock_popen):
        # Test waiting for a response that arrives
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Simulate a response arriving
        response_data = {"id": 1, "result": "test_result"}
        client._responses[1] = response_data

        result = client._wait_for_response(1, timeout=1)

        self.assertEqual(result, response_data)
        # Response should be removed from dict after retrieval
        self.assertNotIn(1, client._responses)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_wait_for_response_timeout(self, mock_popen):
        # Test timeout when waiting for response
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        with self.assertRaises(TimeoutError) as context:
            client._wait_for_response(999, timeout=1)  # Use int instead of float

        self.assertIn("Timed out", str(context.exception))

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_flatten_symbols(self, mock_popen):
        # Test flattening hierarchical symbols
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client.symbol_kinds = [5, 12]  # Class and Function kinds

        symbols = [
            {"kind": 5, "name": "Class1"},
            {
                "kind": 5,
                "name": "Class2",
                "children": [
                    {"kind": 12, "name": "method1"},
                    {"kind": 12, "name": "method2"},
                ],
            },
        ]

        flat = client._flatten_symbols(symbols)

        self.assertEqual(len(flat), 4)
        self.assertIn({"kind": 5, "name": "Class1"}, flat)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_create_qualified_name(self, mock_popen):
        # Test creating qualified names
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Create a file within the project
        test_file = self.project_path / "subdir" / "test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        qualified_name = client._create_qualified_name(test_file, "MyClass")

        self.assertIn("subdir", qualified_name)
        self.assertIn("MyClass", qualified_name)
        self.assertIn(".", qualified_name)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_create_qualified_name_outside_project(self, mock_popen):
        # Test qualified name for file outside project
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        external_file = Path("/external/path/file.py")
        qualified_name = client._create_qualified_name(external_file, "ExternalClass")

        # Should use filename when outside project
        self.assertIn("file.py", qualified_name)
        self.assertIn("ExternalClass", qualified_name)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_call_positions_in_range(self, mock_popen):
        # Test finding function call positions
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = """def test_function():
    result = helper_function(arg1, arg2)
    data.process()
    if something():
        return value
"""

        positions = client._find_call_positions_in_range(content, 0, 5)

        # Should find: helper_function, process, something
        self.assertGreaterEqual(len(positions), 3)

        # Check that function names are extracted
        names = [pos["name"] for pos in positions]
        self.assertIn("helper_function", names)
        self.assertIn("process", names)
        self.assertIn("something", names)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_call_positions_skip_keywords(self, mock_popen):
        # Test that keywords are not identified as function calls
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = """if (condition):
    for (item in items):
        while (True):
            pass
"""

        positions = client._find_call_positions_in_range(content, 0, 4)

        # Should not find 'if', 'for', 'while' as function calls
        names = [pos["name"] for pos in positions]
        self.assertNotIn("if", names)
        self.assertNotIn("for", names)
        self.assertNotIn("while", names)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_package_name(self, mock_popen):
        # Test extracting package name from file path
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Create a nested file
        test_file = self.project_path / "package1" / "subpackage" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        package_name = client._get_package_name(test_file)

        self.assertEqual(package_name, "package1.subpackage")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_package_name_root_level(self, mock_popen):
        # Test package name for root level file
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        test_file = self.project_path / "module.py"
        test_file.touch()

        package_name = client._get_package_name(test_file)

        self.assertEqual(package_name, "root")

    def test_extract_package_from_import(self):
        # Test extracting top-level package from import
        # This is a static method, no client needed
        result = LSPClient._extract_package_from_import("os.path.join")
        self.assertEqual(result, "os")

        result = LSPClient._extract_package_from_import("django.http.response")
        self.assertEqual(result, "django")

        result = LSPClient._extract_package_from_import("mypackage")
        self.assertEqual(result, "mypackage")

    def test_extract_package_from_import_relative(self):
        # Test relative imports
        result = LSPClient._extract_package_from_import(".module")
        self.assertEqual(result, "")

        result = LSPClient._extract_package_from_import("..package")
        self.assertEqual(result, "")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_filter_src_files(self, mock_popen):
        # Test filtering source files
        import pathspec

        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "src").mkdir(exist_ok=True)
        (self.project_path / "test").mkdir(exist_ok=True)
        (self.project_path / ".hidden").mkdir(exist_ok=True)

        src_file = self.project_path / "src" / "main.py"
        test_file = self.project_path / "test" / "test_main.py"
        hidden_file = self.project_path / ".hidden" / "file.py"

        src_file.touch()
        test_file.touch()
        hidden_file.touch()

        src_files = [src_file, test_file, hidden_file]

        # Create empty pathspec
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [])

        filtered = client.filter_src_files(src_files, spec)

        # Should exclude test files and hidden directory files
        self.assertIn(src_file, filtered)
        self.assertNotIn(test_file, filtered)
        self.assertNotIn(hidden_file, filtered)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_exclude_dirs_no_gitignore(self, mock_popen):
        # Test get_exclude_dirs when no .gitignore exists
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        spec = client.get_exclude_dirs()

        # Should return empty pathspec
        self.assertEqual(spec.match_file("anyfile.py"), False)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_exclude_dirs_with_gitignore(self, mock_popen):
        # Test get_exclude_dirs with .gitignore file
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        # Create .gitignore
        gitignore = self.project_path / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n")

        client = LSPClient(self.project_path, self.mock_language)

        spec = client.get_exclude_dirs()

        # Should match patterns from .gitignore
        self.assertTrue(spec.match_file("test.pyc"))
        self.assertTrue(spec.match_file("__pycache__/file.py"))
        self.assertFalse(spec.match_file("test.py"))

    @patch("static_analyzer.lsp_client.client.threading.Thread")
    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_close(self, mock_popen, mock_thread):
        # Test closing the LSP client
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_process.wait = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._initialize = Mock()  # type: ignore[method-assign]
        client.start()

        # Mock _wait_for_response to avoid timeout
        client._wait_for_response = Mock(return_value={"result": None})  # type: ignore[method-assign]

        client.close()

        # Verify shutdown sequence
        client._wait_for_response.assert_called_once()
        mock_process.terminate.assert_called_once()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_close_with_timeout(self, mock_popen):
        # Test closing when process doesn't terminate gracefully
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_process.wait = Mock(side_effect=subprocess.TimeoutExpired("cmd", 5))
        mock_process.kill = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process
        client._shutdown_flag = threading.Event()
        client._wait_for_response = Mock(return_value={"result": None})  # type: ignore[method-assign]

        client.close()

        # Verify kill was called after timeout
        mock_process.kill.assert_called_once()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_source_files(self, mock_popen):
        # Test getting source files
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "file1.py").touch()
        (self.project_path / "subdir").mkdir()
        (self.project_path / "subdir" / "file2.py").touch()
        (self.project_path / "other.txt").touch()

        src_files = client._get_source_files()

        # Should find .py files
        self.assertEqual(len(src_files), 2)
        py_files = [f.name for f in src_files]
        self.assertIn("file1.py", py_files)
        self.assertIn("file2.py", py_files)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_classes_in_symbols(self, mock_popen):
        # Test finding class symbols
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        symbols = [
            {"kind": 5, "name": "Class1"},
            {"kind": 12, "name": "function1"},
            {
                "kind": 5,
                "name": "Class2",
                "children": [
                    {"kind": 5, "name": "InnerClass"},
                    {"kind": 6, "name": "method"},
                ],
            },
        ]

        classes = client._find_classes_in_symbols(symbols)

        # Should find 3 classes (Class1, Class2, InnerClass)
        self.assertEqual(len(classes), 3)
        class_names = [c["name"] for c in classes]
        self.assertIn("Class1", class_names)
        self.assertIn("Class2", class_names)
        self.assertIn("InnerClass", class_names)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_all_symbols_recursive(self, mock_popen):
        # Test recursively collecting all symbols
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        symbols = [
            {"kind": 5, "name": "Class1"},
            {
                "kind": 5,
                "name": "Class2",
                "children": [
                    {"kind": 12, "name": "method1"},
                    {
                        "kind": 12,
                        "name": "method2",
                        "children": [{"kind": 13, "name": "variable"}],
                    },
                ],
            },
        ]

        all_symbols = client._get_all_symbols_recursive(symbols)

        # Should find all 5 symbols
        self.assertEqual(len(all_symbols), 5)
        names = [s["name"] for s in all_symbols]
        self.assertIn("Class1", names)
        self.assertIn("Class2", names)
        self.assertIn("method1", names)
        self.assertIn("method2", names)
        self.assertIn("variable", names)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_read_messages_with_response(self, mock_popen):
        # Test _read_messages handling responses
        mock_process = Mock()
        mock_stdout = Mock()

        # Simulate server response
        response_body = json.dumps({"id": 1, "result": "test_value"})
        response_msg = f"Content-Length: {len(response_body)}\r\n\r\n{response_body}"

        # Mock readline to return header, blank line, then keep returning empty bytes
        def readline_side_effect():
            yield "Content-Length: 35\r\n".encode("utf-8")
            yield b"\r\n"  # blank line after header
            # Keep yielding empty bytes to prevent StopIteration
            while True:
                yield b""

        mock_stdout.readline.side_effect = readline_side_effect()
        mock_stdout.read.return_value = response_body.encode("utf-8")

        mock_process.stdout = mock_stdout
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process
        client._shutdown_flag = threading.Event()

        # Run reader in a thread briefly
        thread = threading.Thread(target=client._read_messages)
        thread.daemon = True
        thread.start()
        time.sleep(0.1)
        client._shutdown_flag.set()
        thread.join(timeout=1)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_read_messages_with_notification(self, mock_popen):
        # Test _read_messages handling notifications
        mock_process = Mock()
        mock_stdout = Mock()

        # Notification has no 'id' field
        notification_body = json.dumps({"method": "textDocument/publishDiagnostics", "params": {}})

        # Mock readline to return header, blank line, then keep returning empty bytes
        def readline_side_effect():
            yield f"Content-Length: {len(notification_body)}\r\n".encode("utf-8")
            yield b"\r\n"  # blank line after header
            # Keep yielding empty bytes to prevent StopIteration
            while True:
                yield b""

        mock_stdout.readline.side_effect = readline_side_effect()
        mock_stdout.read.return_value = notification_body.encode("utf-8")

        mock_process.stdout = mock_stdout
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process
        client._shutdown_flag = threading.Event()

        # Run reader briefly
        thread = threading.Thread(target=client._read_messages)
        thread.daemon = True
        thread.start()
        time.sleep(0.1)
        client._shutdown_flag.set()
        thread.join(timeout=1)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_initialize_with_error(self, mock_popen):
        # Test initialization that returns an error
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Mock error response
        client._wait_for_response = Mock(return_value={"error": {"message": "Init failed"}})  # type: ignore[method-assign]

        with self.assertRaises(RuntimeError) as context:
            client._initialize()

        self.assertIn("Initialization failed", str(context.exception))

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_document_symbols(self, mock_popen):
        # Test getting document symbols
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_symbols = [{"name": "TestClass", "kind": 5}]
        client._wait_for_response = Mock(return_value={"result": test_symbols})  # type: ignore[method-assign]

        result = client._get_document_symbols("file:///test.py")

        self.assertEqual(result, test_symbols)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_prepare_call_hierarchy(self, mock_popen):
        # Test preparing call hierarchy
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_items = [{"name": "test_function", "uri": "file:///test.py"}]
        client._wait_for_response = Mock(return_value={"result": test_items})  # type: ignore[method-assign]

        result = client._prepare_call_hierarchy("file:///test.py", 10, 5)

        self.assertEqual(result, test_items)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_incoming_calls(self, mock_popen):
        # Test getting incoming calls
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_calls = [{"from": {"name": "caller", "uri": "file:///caller.py"}}]
        client._wait_for_response = Mock(return_value={"result": test_calls})  # type: ignore[method-assign]

        item = {"name": "test_func"}
        result = client._get_incoming_calls(item)

        self.assertEqual(result, test_calls)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_outgoing_calls(self, mock_popen):
        # Test getting outgoing calls
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_calls = [{"to": {"name": "callee", "uri": "file:///callee.py"}}]
        client._wait_for_response = Mock(return_value={"result": test_calls})  # type: ignore[method-assign]

        item = {"name": "test_func"}
        result = client._get_outgoing_calls(item)

        self.assertEqual(result, test_calls)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_call_position_internal(self, mock_popen):
        # Test resolving a call position to internal project file
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Create a test file
        test_file = self.project_path / "module.py"
        test_file.touch()

        # Mock definition pointing to project file
        definition = [{"uri": test_file.as_uri(), "range": {"start": {"line": 0}}}]
        client._get_definition_for_position = Mock(return_value=definition)  # type: ignore[method-assign]

        call_pos = {"line": 5, "char": 10, "name": "helper"}
        result = client._resolve_call_position("file:///test.py", Path("/test.py"), call_pos)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("helper", result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_call_position_external(self, mock_popen):
        # Test resolving a call position to external library
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Mock definition pointing to external file using URI directly
        definition = [{"uri": "file:///external/lib/module.py", "range": {"start": {"line": 0}}}]
        client._get_definition_for_position = Mock(return_value=definition)  # type: ignore[method-assign]

        call_pos = {"line": 5, "char": 10, "name": "external_func"}
        result = client._resolve_call_position("file:///test.py", Path("/test.py"), call_pos)

        # Should return simple name for external
        self.assertEqual(result, "external_func")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_call_position_unresolved(self, mock_popen):
        # Test resolving a call position that cannot be resolved
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # No definition found
        client._get_definition_for_position = Mock(return_value=[])  # type: ignore[method-assign]

        call_pos = {"line": 5, "char": 10, "name": "unknown_func"}
        result = client._resolve_call_position("file:///test.py", Path("/test.py"), call_pos)

        self.assertIsNone(result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_definition_for_position(self, mock_popen):
        # Test getting definition for a position
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_def = [{"uri": "file:///def.py", "range": {"start": {"line": 10}}}]
        client._wait_for_response = Mock(return_value={"result": test_def})  # type: ignore[method-assign]

        result = client._get_definition_for_position("file:///test.py", 5, 10)

        self.assertEqual(result, test_def)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_definition_for_position_with_error(self, mock_popen):
        # Test getting definition with error response
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        client._wait_for_response = Mock(return_value={"error": {"message": "Not found"}})  # type: ignore[method-assign]

        result = client._get_definition_for_position("file:///test.py", 5, 10)

        self.assertEqual(result, [])

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_extract_superclasses_from_text(self, mock_popen):
        # Test extracting superclasses from text
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = """
class BaseClass:
    pass

class DerivedClass(BaseClass):
    pass
"""
        test_file = self.project_path / "test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        # Mock _resolve_class_name to return a qualified name
        client._resolve_class_name = Mock(return_value="test.BaseClass")  # type: ignore[method-assign]

        result = client._extract_superclasses_from_text(test_file, "DerivedClass", content)

        self.assertIn("test.BaseClass", result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_class_name_with_import(self, mock_popen):
        # Test resolving a class name using imports
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = """
from module.submodule import MyClass

class DerivedClass(MyClass):
    pass
"""
        test_file = self.project_path / "test.py"

        result = client._resolve_class_name("MyClass", test_file, content)

        self.assertEqual(result, "module.submodule.MyClass")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_class_name_same_package(self, mock_popen):
        # Test resolving a class name in same package
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = "class SomeClass: pass"

        test_file = self.project_path / "mypackage" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        result = client._resolve_class_name("OtherClass", test_file, content)

        self.assertIn("mypackage", result)
        self.assertIn("OtherClass", result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_references(self, mock_popen):
        # Test getting references
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_refs = [{"uri": "file:///ref.py", "range": {"start": {"line": 5}}}]
        client._wait_for_response = Mock(return_value={"result": test_refs})  # type: ignore[method-assign]

        result = client._get_references("file:///test.py", 10, 5)

        self.assertEqual(result, test_refs)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_extract_package_from_reference(self, mock_popen):
        # Test extracting package from reference
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        test_file = self.project_path / "pkg" / "module.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        reference = {"uri": test_file.as_uri()}
        result = client._extract_package_from_reference(reference)

        self.assertEqual(result, "pkg")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_extract_imports_from_symbols(self, mock_popen):
        # Test extracting imports from symbols and content
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        content = """
import os
import sys
from pathlib import Path
from typing import List, Dict

def my_function():
    pass
"""
        symbols: list[dict] = []

        result = client._extract_imports_from_symbols(symbols, content)

        self.assertIn("os", result)
        self.assertIn("sys", result)
        self.assertIn("pathlib", result)
        self.assertIn("typing", result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_package_name_external(self, mock_popen):
        # Test package name for file outside project
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        external_file = Path("/external/module.py")
        result = client._get_package_name(external_file)

        self.assertEqual(result, "external")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_prepare_for_analysis(self, mock_popen):
        # Test prepare_for_analysis (default implementation does nothing)
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Should not raise any exception
        client._prepare_for_analysis()

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_all_classes_in_workspace(self, mock_popen):
        # Test getting all classes in workspace
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_symbols = [
            {"name": "Class1", "kind": 5},
            {"name": "function1", "kind": 12},
            {"name": "Class2", "kind": 5},
        ]
        client._wait_for_response = Mock(return_value={"result": test_symbols})  # type: ignore[method-assign]

        result = client._get_all_classes_in_workspace()

        # Should only return class symbols (kind 5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Class1")
        self.assertEqual(result[1]["name"], "Class2")

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_all_classes_in_workspace_with_error(self, mock_popen):
        # Test getting classes with error
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        client._wait_for_response = Mock(return_value={"error": {"message": "Failed"}})  # type: ignore[method-assign]

        result = client._get_all_classes_in_workspace()

        self.assertEqual(result, [])

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_superclasses_via_definition(self, mock_popen):
        # Test finding superclasses via LSP definition
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        content = """
class DerivedClass(BaseClass):
    pass
"""

        # Create a base class file
        base_file = self.project_path / "base.py"
        base_file.write_text("class BaseClass:\n    pass\n")

        class_symbol = {"name": "DerivedClass", "range": {"start": {"line": 1}, "end": {"line": 2}}}

        # Mock definition pointing to base class
        definition = [{"uri": base_file.as_uri(), "range": {"start": {"line": 0}, "end": {"line": 1}}}]

        client._get_definition_for_position = Mock(return_value=definition)  # type: ignore[method-assign]

        result = client._find_superclasses_via_definition("file:///test.py", class_symbol, content)

        # Should find the base class
        self.assertGreater(len(result), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_subclasses(self, mock_popen):
        # Test finding subclasses
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Create a derived class file
        derived_file = self.project_path / "derived.py"
        derived_file.write_text("class DerivedClass(BaseClass):\n    pass\n")

        class_symbol = {"name": "BaseClass", "selectionRange": {"start": {"line": 0, "character": 6}}}

        # Mock references pointing to derived class
        references = [{"uri": derived_file.as_uri(), "range": {"start": {"line": 0}, "end": {"line": 1}}}]

        client._get_references = Mock(return_value=references)  # type: ignore[method-assign]

        result = client._find_subclasses("file:///base.py", class_symbol, [])

        # Should find derived classes
        self.assertIsInstance(result, list)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_find_external_references(self, mock_popen):
        # Test finding external references
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        symbols = [{"name": "my_function", "kind": 12, "selectionRange": {"start": {"line": 5, "character": 4}}}]

        test_refs = [{"uri": "file:///other.py", "range": {"start": {"line": 10}}}]
        client._get_references = Mock(return_value=test_refs)  # type: ignore[method-assign]

        result = client._find_external_references("file:///test.py", symbols)

        self.assertEqual(result, test_refs)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_build_static_analysis_no_files(self, mock_popen):
        # Test build_static_analysis with no source files
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Mock to return no files
        client._get_source_files = Mock(return_value=[])  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=Mock())  # type: ignore[method-assign]

        result = client.build_static_analysis()

        self.assertIn("call_graph", result)
        self.assertIn("class_hierarchies", result)
        self.assertIn("package_relations", result)
        self.assertIn("references", result)
        self.assertEqual(len(result["references"]), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_resolve_call_position_with_exception(self, mock_popen):
        # Test resolve_call_position handling exceptions
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Mock to raise exception
        client._get_definition_for_position = Mock(side_effect=Exception("Test error"))  # type: ignore[method-assign]

        call_pos = {"line": 5, "char": 10, "name": "func"}
        result = client._resolve_call_position("file:///test.py", Path("/test.py"), call_pos)

        # Should return None on exception
        self.assertIsNone(result)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_definition_with_exception(self, mock_popen):
        # Test _get_definition handling exceptions
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        client._wait_for_response = Mock(side_effect=Exception("Test error"))  # type: ignore[method-assign]

        result = client._get_definition(5, 10)

        self.assertEqual(result, [])

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_get_references_with_exception(self, mock_popen):
        # Test _get_references handling exceptions
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        client._wait_for_response = Mock(side_effect=Exception("Test error"))  # type: ignore[method-assign]

        result = client._get_references("file:///test.py", 5, 10)

        self.assertEqual(result, [])

    @patch("static_analyzer.lsp_client.client.ThreadPoolExecutor")
    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_build_static_analysis_with_files(self, mock_popen, mock_executor):
        # Test build_static_analysis with source files
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Create test files
        test_file1 = self.project_path / "module1.py"
        test_file2 = self.project_path / "module2.py"
        test_file1.write_text("def func1(): pass")
        test_file2.write_text("def func2(): pass")

        # Mock methods
        client._get_source_files = Mock(return_value=[test_file1, test_file2])  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=Mock())  # type: ignore[method-assign]
        client.filter_src_files = Mock(return_value=[test_file1, test_file2])  # type: ignore[method-assign]
        client._prepare_for_analysis = Mock()  # type: ignore[method-assign]
        client._get_all_classes_in_workspace = Mock(return_value=[])  # type: ignore[method-assign]

        # Create mock results
        result1 = FileAnalysisResult(
            file_path=test_file1,
            package_name="root",
            imports=["os"],
            symbols=[Node("root.func1", 12, str(test_file1), 0, 1)],
            function_symbols=[{"name": "func1", "kind": 12, "selectionRange": {"start": {"line": 0, "character": 4}}}],
            class_symbols=[],
            call_relationships=[],
            class_hierarchies={},
            external_references=[],
        )

        result2 = FileAnalysisResult(
            file_path=test_file2,
            package_name="root",
            imports=["sys"],
            symbols=[Node("root.func2", 12, str(test_file2), 0, 1)],
            function_symbols=[],
            class_symbols=[],
            call_relationships=[("root.func2", "root.func1")],
            class_hierarchies={},
            external_references=[],
        )

        # Mock executor
        mock_future1 = Mock()
        mock_future1.result.return_value = result1
        mock_future2 = Mock()
        mock_future2.result.return_value = result2

        mock_executor_instance = Mock()
        mock_executor_instance.__enter__ = Mock(return_value=mock_executor_instance)
        mock_executor_instance.__exit__ = Mock(return_value=None)
        mock_executor_instance.submit = Mock(side_effect=[mock_future1, mock_future2])
        mock_executor.return_value = mock_executor_instance

        # Mock as_completed to return futures
        with patch("static_analyzer.lsp_client.client.as_completed", return_value=[mock_future1, mock_future2]):
            result = client.build_static_analysis()

        # Verify result structure
        self.assertIn("call_graph", result)
        self.assertIn("class_hierarchies", result)
        self.assertIn("package_relations", result)
        self.assertIn("references", result)
        self.assertEqual(len(result["references"]), 2)
        self.assertIn("root", result["package_relations"])

    @patch("static_analyzer.lsp_client.client.ThreadPoolExecutor")
    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_build_static_analysis_with_errors(self, mock_popen, mock_executor):
        # Test build_static_analysis handling file processing errors
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "module.py"
        test_file.write_text("def func(): pass")

        client._get_source_files = Mock(return_value=[test_file])  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=Mock())  # type: ignore[method-assign]
        client.filter_src_files = Mock(return_value=[test_file])  # type: ignore[method-assign]
        client._prepare_for_analysis = Mock()  # type: ignore[method-assign]
        client._get_all_classes_in_workspace = Mock(return_value=[])  # type: ignore[method-assign]

        # Create result with error
        error_result = FileAnalysisResult(
            file_path=test_file,
            package_name="root",
            imports=[],
            symbols=[],
            function_symbols=[],
            class_symbols=[],
            call_relationships=[],
            class_hierarchies={},
            external_references=[],
            error="Test error",
        )

        mock_future = Mock()
        mock_future.result.return_value = error_result

        mock_executor_instance = Mock()
        mock_executor_instance.__enter__ = Mock(return_value=mock_executor_instance)
        mock_executor_instance.__exit__ = Mock(return_value=None)
        mock_executor_instance.submit = Mock(return_value=mock_future)
        mock_executor.return_value = mock_executor_instance

        with patch("static_analyzer.lsp_client.client.as_completed", return_value=[mock_future]):
            result = client.build_static_analysis()

        # Should still return valid structure even with errors
        self.assertIn("call_graph", result)
        self.assertEqual(len(result["references"]), 0)

    @patch("static_analyzer.lsp_client.client.ThreadPoolExecutor")
    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_build_static_analysis_with_exception(self, mock_popen, mock_executor):
        # Test build_static_analysis handling exceptions during file processing
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "module.py"
        test_file.write_text("def func(): pass")

        client._get_source_files = Mock(return_value=[test_file])  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=Mock())  # type: ignore[method-assign]
        client.filter_src_files = Mock(return_value=[test_file])  # type: ignore[method-assign]
        client._prepare_for_analysis = Mock()  # type: ignore[method-assign]
        client._get_all_classes_in_workspace = Mock(return_value=[])  # type: ignore[method-assign]

        # Mock future that raises exception
        mock_future = Mock()
        mock_future.result.side_effect = Exception("Processing error")

        mock_executor_instance = Mock()
        mock_executor_instance.__enter__ = Mock(return_value=mock_executor_instance)
        mock_executor_instance.__exit__ = Mock(return_value=None)
        mock_executor_instance.submit = Mock(return_value=mock_future)
        mock_executor.return_value = mock_executor_instance

        with patch("static_analyzer.lsp_client.client.as_completed", return_value=[mock_future]):
            result = client.build_static_analysis()

        # Should handle exception gracefully
        self.assertIn("call_graph", result)
        self.assertEqual(len(result["references"]), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_success(self, mock_popen):
        # Test successful analysis of a single file
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        # Create test file
        test_file = self.project_path / "test_module.py"
        test_content = """
import os

class TestClass:
    def method1(self):
        helper_function()

def helper_function():
    pass
"""
        test_file.write_text(test_content)

        # Mock LSP responses
        test_symbols = [
            {
                "name": "TestClass",
                "kind": 5,
                "range": {"start": {"line": 3}, "end": {"line": 5}},
                "selectionRange": {"start": {"line": 3, "character": 6}},
                "children": [
                    {
                        "name": "method1",
                        "kind": 6,
                        "range": {"start": {"line": 4}, "end": {"line": 5}},
                        "selectionRange": {"start": {"line": 4, "character": 8}},
                    }
                ],
            },
            {
                "name": "helper_function",
                "kind": 12,
                "range": {"start": {"line": 7}, "end": {"line": 8}},
                "selectionRange": {"start": {"line": 7, "character": 4}},
            },
        ]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=["os"])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_superclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_subclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._resolve_call_position = Mock(return_value=None)  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Verify result
        self.assertEqual(result.file_path, test_file)
        self.assertEqual(result.package_name, "root")
        self.assertIn("os", result.imports)
        self.assertIsNone(result.error)
        self.assertGreater(len(result.symbols), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_no_symbols(self, mock_popen):
        # Test analysis when file has no symbols
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "empty.py"
        test_file.write_text("# Empty file\n")

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=[])  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Should return early with minimal result
        self.assertEqual(result.file_path, test_file)
        self.assertEqual(len(result.symbols), 0)
        self.assertIsNone(result.error)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_with_exception(self, mock_popen):
        # Test analysis handling exceptions
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "test.py"
        test_file.write_text("def func(): pass")

        # Mock to raise exception
        client._send_notification = Mock(side_effect=Exception("LSP error"))  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Should capture error
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("LSP error", result.error)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_with_calls(self, mock_popen):
        # Test analysis with function calls
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "calls.py"
        test_content = """
def caller():
    callee()

def callee():
    pass
"""
        test_file.write_text(test_content)

        test_symbols = [
            {
                "name": "caller",
                "kind": 12,
                "range": {"start": {"line": 1}, "end": {"line": 2}},
                "selectionRange": {"start": {"line": 1, "character": 4}},
            },
            {
                "name": "callee",
                "kind": 12,
                "range": {"start": {"line": 4}, "end": {"line": 5}},
                "selectionRange": {"start": {"line": 4, "character": 4}},
            },
        ]

        # Mock call hierarchy
        hierarchy_items = [{"name": "caller", "uri": test_file.as_uri()}]
        outgoing_calls = [{"to": {"name": "callee", "uri": test_file.as_uri()}}]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=hierarchy_items)  # type: ignore[method-assign]
        client._get_outgoing_calls = Mock(return_value=outgoing_calls)  # type: ignore[method-assign]
        client._get_incoming_calls = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_superclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_subclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._resolve_call_position = Mock(return_value=None)  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Verify call relationships were captured
        self.assertGreater(len(result.call_relationships), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_with_classes(self, mock_popen):
        # Test analysis with class hierarchies
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "classes.py"
        test_content = """
class BaseClass:
    pass

class DerivedClass(BaseClass):
    pass
"""
        test_file.write_text(test_content)

        test_symbols = [
            {
                "name": "BaseClass",
                "kind": 5,
                "range": {"start": {"line": 1}, "end": {"line": 2}},
                "selectionRange": {"start": {"line": 1, "character": 6}},
            },
            {
                "name": "DerivedClass",
                "kind": 5,
                "range": {"start": {"line": 4}, "end": {"line": 5}},
                "selectionRange": {"start": {"line": 4, "character": 6}},
            },
        ]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_superclasses = Mock(return_value=["root.BaseClass"])  # type: ignore[method-assign]
        client._find_subclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]
        client._resolve_call_position = Mock(return_value=None)  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Verify class hierarchies were captured
        self.assertEqual(len(result.class_symbols), 2)
        self.assertGreater(len(result.class_hierarchies), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_with_body_calls(self, mock_popen):
        # Test analysis capturing calls in function bodies
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "body_calls.py"
        test_content = """
def main():
    helper1()
    helper2()
"""
        test_file.write_text(test_content)

        test_symbols = [
            {
                "name": "main",
                "kind": 12,
                "range": {"start": {"line": 1}, "end": {"line": 3}},
                "selectionRange": {"start": {"line": 1, "character": 4}},
            }
        ]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_superclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_subclasses = Mock(return_value=[])  # type: ignore[method-assign]
        client._resolve_call_position = Mock(return_value="root.helper1")  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Verify function body calls were analyzed
        self.assertIsNone(result.error)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_body_calls_exception(self, mock_popen):
        # Test analysis handling exceptions in body call processing
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "error_calls.py"
        test_file.write_text("def func():\n    call()\n")

        test_symbols = [
            {
                "name": "func",
                "kind": 12,
                "range": {"start": {"line": 0}, "end": {"line": 1}},
                "selectionRange": {"start": {"line": 0, "character": 4}},
            }
        ]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]
        # Make _find_call_positions_in_range raise exception
        client._find_call_positions_in_range = Mock(side_effect=Exception("Parse error"))  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Should handle exception gracefully and still complete
        self.assertIsNone(result.error)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_incoming_calls(self, mock_popen):
        # Test analysis capturing incoming calls
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "incoming.py"
        test_file.write_text("def target():\n    pass\n")

        test_symbols = [
            {
                "name": "target",
                "kind": 12,
                "range": {"start": {"line": 0}, "end": {"line": 1}},
                "selectionRange": {"start": {"line": 0, "character": 4}},
            }
        ]

        hierarchy_items = [{"name": "target", "uri": test_file.as_uri()}]
        incoming_calls = [{"from": {"name": "caller", "uri": test_file.as_uri()}}]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=hierarchy_items)  # type: ignore[method-assign]
        client._get_outgoing_calls = Mock(return_value=[])  # type: ignore[method-assign]
        client._get_incoming_calls = Mock(return_value=incoming_calls)  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Verify incoming calls were captured
        self.assertGreater(len(result.call_relationships), 0)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_analyze_single_file_call_hierarchy_exception(self, mock_popen):
        # Test handling exceptions in call hierarchy processing
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)
        client._process = mock_process

        test_file = self.project_path / "func.py"
        test_file.write_text("def func(): pass")

        test_symbols = [
            {
                "name": "func",
                "kind": 12,
                "range": {"start": {"line": 0}, "end": {"line": 0}},
                "selectionRange": {"start": {"line": 0, "character": 4}},
            }
        ]

        hierarchy_items = [{"name": "func", "uri": test_file.as_uri()}]

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._get_document_symbols = Mock(return_value=test_symbols)  # type: ignore[method-assign]
        client._get_package_name = Mock(return_value="root")  # type: ignore[method-assign]
        client._extract_imports_from_symbols = Mock(return_value=[])  # type: ignore[method-assign]
        client._prepare_call_hierarchy = Mock(return_value=hierarchy_items)  # type: ignore[method-assign]
        # Make outgoing calls raise exception
        client._get_outgoing_calls = Mock(side_effect=Exception("Call error"))  # type: ignore[method-assign]
        client._get_incoming_calls = Mock(return_value=[])  # type: ignore[method-assign]
        client._find_external_references = Mock(return_value=[])  # type: ignore[method-assign]

        result = client._analyze_single_file(test_file, [])

        # Should handle exception and continue
        self.assertIsNone(result.error)

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_handle_notification_default_implementation(self, mock_popen):
        """Test default handle_notification implementation does nothing."""
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Test notification with default handler - should not crash
        client.handle_notification("window/logMessage", {"message": "Test log"})  # Should not raise

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_handle_notification_calls_subclass_handler(self, mock_popen):
        """Test that _handle_notification calls the handle_notification method."""
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Mock the handle_notification method
        with patch.object(client, "handle_notification") as mock_handler:
            # Test notification
            notification = {"method": "textDocument/publishDiagnostics", "params": {"diagnostics": []}}
            client._handle_notification(notification)

            # Should call the handler
            mock_handler.assert_called_once_with("textDocument/publishDiagnostics", {"diagnostics": []})

    @patch("static_analyzer.lsp_client.client.subprocess.Popen")
    def test_handle_notification_handles_handler_exception(self, mock_popen):
        """Test that _handle_notification handles exceptions in handle_notification."""
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = LSPClient(self.project_path, self.mock_language)

        # Mock handle_notification to raise an exception
        with patch.object(client, "handle_notification", side_effect=Exception("Handler error")):
            # Test notification - should not propagate exception
            notification = {"method": "test/notification", "params": {}}
            client._handle_notification(notification)  # Should not raise


if __name__ == "__main__":
    unittest.main()
