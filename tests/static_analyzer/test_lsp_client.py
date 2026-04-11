"""Tests for static_analyzer.engine.lsp_client.LSPClient.

These tests mock subprocess/IO to test the protocol logic
without starting a real LSP server.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.engine.lsp_client import (
    LSP_METHOD_NOT_FOUND,
    LSPClient,
    MethodNotFoundError,
)


class TestLSPClientInit:
    def test_default_attributes(self):
        client = LSPClient(["fake-server"], Path("/project"))
        assert client._command == ["fake-server"]
        assert client._default_timeout == 60
        assert client._collect_diagnostics is False
        assert client._process is None

    def test_custom_timeout(self):
        client = LSPClient(["cmd"], Path("/root"), default_timeout=120)
        assert client._default_timeout == 120

    def test_diagnostics_collection_flag(self):
        client = LSPClient(["cmd"], Path("/root"), collect_diagnostics=True)
        assert client._collect_diagnostics is True


class TestWriteMessage:
    def test_writes_json_rpc_with_content_length(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        client._process = mock_process

        client._write_message({"jsonrpc": "2.0", "id": 1, "method": "test"})

        mock_stdin.write.assert_called_once()
        written = mock_stdin.write.call_args[0][0]
        assert b"Content-Length:" in written
        assert b'"jsonrpc": "2.0"' in written
        mock_stdin.flush.assert_called_once()

    def test_raises_when_not_running(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = None

        with pytest.raises(RuntimeError, match="LSP server not running"):
            client._write_message({"test": True})


class TestNextResponse:
    def test_returns_response_from_queue(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        response = {"jsonrpc": "2.0", "id": 1, "result": {"key": "value"}}
        client._msg_queue.put(response)

        import time

        result = client._next_response(time.monotonic() + 5)
        assert result == response

    def test_returns_none_on_timeout(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        import time

        result = client._next_response(time.monotonic() + 0.01)
        assert result is None

    def test_returns_none_when_deadline_passed(self):
        client = LSPClient(["cmd"], Path("/root"))

        import time

        result = client._next_response(time.monotonic() - 1)
        assert result is None

    def test_handles_server_initiated_request(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        # Server-initiated request (has both 'method' and 'id')
        server_req = {"jsonrpc": "2.0", "id": 99, "method": "workspace/configuration", "params": {}}
        client._msg_queue.put(server_req)

        import time

        result = client._next_response(time.monotonic() + 5)
        # Should respond to server and return None
        assert result is None
        mock_stdin.write.assert_called_once()

    def test_skips_notifications(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        # Notification (no 'id')
        notification = {"jsonrpc": "2.0", "method": "some/notification", "params": {}}
        client._msg_queue.put(notification)

        import time

        result = client._next_response(time.monotonic() + 5)
        assert result is None

    def test_raises_on_dead_server(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = 1
        client._process.returncode = 1

        import time

        with pytest.raises(RuntimeError, match="LSP server process exited"):
            client._next_response(time.monotonic() + 5)


class TestDocumentSymbol:
    def test_returns_list_result(self):
        client = LSPClient(["cmd"], Path("/root"))
        symbols = [{"name": "foo", "kind": 12}]

        with patch.object(client, "_send_request", return_value=symbols):
            result = client.document_symbol(Path("/root/test.py"))

        assert result == symbols

    def test_returns_empty_list_for_none(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_request", return_value=None):
            result = client.document_symbol(Path("/root/test.py"))

        assert result == []


class TestReferences:
    def test_returns_list_result(self):
        client = LSPClient(["cmd"], Path("/root"))
        refs = [{"uri": "file:///test.py", "range": {}}]

        with patch.object(client, "_send_request", return_value=refs):
            result = client.references(Path("/root/test.py"), 5, 10)

        assert result == refs

    def test_returns_empty_for_none(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_request", return_value=None):
            result = client.references(Path("/root/test.py"), 5, 10)

        assert result == []


class TestSendReferencesBatch:
    def test_sends_batch_and_collects_results(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._request_id = 0

        refs_a = [{"uri": "file:///a.py", "range": {}}]
        refs_b = [{"uri": "file:///b.py", "range": {}}]

        def mock_collect(req_ids, timeout=None):
            return {req_ids[0]: refs_a, req_ids[1]: refs_b}, set(), set()

        with (
            patch.object(client, "_write_message"),
            patch.object(client, "_collect_batch_responses", side_effect=mock_collect),
        ):
            results, error_indices = client.send_references_batch(
                [
                    (Path("/root/a.py"), 1, 0),
                    (Path("/root/b.py"), 2, 0),
                ]
            )

        assert len(results) == 2
        assert results[0] == refs_a
        assert results[1] == refs_b
        assert error_indices == set()

    def test_returns_error_indices_for_failed_requests(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._request_id = 0

        refs_a = [{"uri": "file:///a.py", "range": {}}]

        def mock_collect(req_ids, timeout=None):
            # Second request errored
            return {req_ids[0]: refs_a, req_ids[1]: []}, set(), {req_ids[1]}

        with (
            patch.object(client, "_write_message"),
            patch.object(client, "_collect_batch_responses", side_effect=mock_collect),
        ):
            results, error_indices = client.send_references_batch(
                [
                    (Path("/root/a.py"), 1, 0),
                    (Path("/root/b.py"), 2, 0),
                ]
            )

        assert len(results) == 2
        assert results[0] == refs_a
        assert results[1] == []
        assert error_indices == {1}


class TestTypeHierarchy:
    def test_prepare_returns_list(self):
        client = LSPClient(["cmd"], Path("/root"))
        items = [{"name": "Foo", "kind": 5}]

        with patch.object(client, "_send_request", return_value=items):
            result = client.type_hierarchy_prepare(Path("/root/test.py"), 0, 0)

        assert result == items

    def test_prepare_returns_none_for_non_list(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_request", return_value=None):
            result = client.type_hierarchy_prepare(Path("/root/test.py"), 0, 0)

        assert result is None

    def test_supertypes_returns_list(self):
        client = LSPClient(["cmd"], Path("/root"))
        types = [{"name": "Base"}]

        with patch.object(client, "_send_request", return_value=types):
            result = client.type_hierarchy_supertypes({"name": "Child"})

        assert result == types

    def test_subtypes_returns_empty_for_none(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_request", return_value=None):
            result = client.type_hierarchy_subtypes({"name": "Parent"})

        assert result == []


class TestSendRequest:
    def test_returns_result(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        # Queue a matching response
        response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        client._msg_queue.put(response)

        result = client._send_request("test/method", {"key": "value"}, timeout=5)
        assert result == {"capabilities": {}}

    def test_raises_method_not_found(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": LSP_METHOD_NOT_FOUND, "message": "Method not found"},
        }
        client._msg_queue.put(error_response)

        with pytest.raises(MethodNotFoundError):
            client._send_request("unknown/method", {}, timeout=5)

    def test_returns_none_on_other_error(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "Server error"},
        }
        client._msg_queue.put(error_response)

        result = client._send_request("test/method", {}, timeout=5)
        assert result is None

    def test_raises_timeout(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        # Queue no matching response
        with pytest.raises(TimeoutError):
            client._send_request("test/method", {}, timeout=1)

    def test_skips_non_matching_ids(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.poll.return_value = None
        client._process = mock_process

        # Queue a response with wrong id, then the correct one
        wrong = {"jsonrpc": "2.0", "id": 999, "result": "wrong"}
        correct = {"jsonrpc": "2.0", "id": 1, "result": "correct"}
        client._msg_queue.put(wrong)
        client._msg_queue.put(correct)

        result = client._send_request("test/method", {}, timeout=5)
        assert result == "correct"


class TestCollectBatchResponses:
    def test_collects_all_responses(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        # Queue responses out of order
        client._msg_queue.put({"jsonrpc": "2.0", "id": 2, "result": [{"b": 1}]})
        client._msg_queue.put({"jsonrpc": "2.0", "id": 1, "result": [{"a": 1}]})

        results, timed_out, error_ids = client._collect_batch_responses([1, 2], timeout=5)

        assert results[1] == [{"a": 1}]
        assert results[2] == [{"b": 1}]
        assert timed_out == set()
        assert error_ids == set()

    def test_reports_timed_out_ids(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        # Only queue one of two expected responses
        client._msg_queue.put({"jsonrpc": "2.0", "id": 1, "result": [{"a": 1}]})

        results, timed_out, error_ids = client._collect_batch_responses([1, 2], timeout=1)

        assert results[1] == [{"a": 1}]
        assert results[2] == []
        assert 2 in timed_out
        assert error_ids == set()

    def test_handles_error_responses(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        client._msg_queue.put({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "fail"}})

        results, timed_out, error_ids = client._collect_batch_responses([1], timeout=5)
        assert results[1] == []
        assert timed_out == set()
        assert 1 in error_ids

    def test_deduplicates_error_logging(self, caplog):
        """Repeated LSP errors are logged once with a count, not per-request."""
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = None

        # Queue 3 identical errors
        for i in range(1, 4):
            client._msg_queue.put({"jsonrpc": "2.0", "id": i, "error": {"code": 0, "message": "no package metadata"}})

        import logging

        with caplog.at_level(logging.WARNING):
            _, _, error_ids = client._collect_batch_responses([1, 2, 3], timeout=5)

        assert error_ids == {1, 2, 3}
        # Should have a single deduplicated warning, not 3 separate ones
        error_lines = [r for r in caplog.records if "no package metadata" in r.message]
        assert len(error_lines) == 1
        assert "x3" in error_lines[0].message


class TestHandleNotification:
    def test_diagnostics_notification(self):
        client = LSPClient(["cmd"], Path("/root"), collect_diagnostics=True)

        params = {
            "uri": Path("/root/test.py").as_uri(),
            "diagnostics": [
                {
                    "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 10}},
                    "message": "Unused import",
                    "severity": 2,
                    "code": "W0611",
                }
            ],
        }

        client._handle_notification("textDocument/publishDiagnostics", params)

        diags = client.get_collected_diagnostics()
        assert len(diags) == 1
        file_key = next(iter(diags))
        assert len(diags[file_key]) == 1
        assert diags[file_key][0].message == "Unused import"

    def test_diagnostics_generation_increments(self):
        client = LSPClient(["cmd"], Path("/root"), collect_diagnostics=True)
        assert client.get_diagnostics_generation() == 0

        params = {
            "uri": Path("/root/test.py").as_uri(),
            "diagnostics": [
                {
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
                    "message": "error 1",
                    "severity": 1,
                }
            ],
        }
        client._handle_notification("textDocument/publishDiagnostics", params)
        assert client.get_diagnostics_generation() == 1

        # Second notification for a different file bumps again
        params["uri"] = Path("/root/other.py").as_uri()
        client._handle_notification("textDocument/publishDiagnostics", params)
        assert client.get_diagnostics_generation() == 2

    def test_service_ready_notification(self):
        client = LSPClient(["cmd"], Path("/root"))
        assert not client._server_ready.is_set()

        client._handle_notification("language/status", {"type": "ServiceReady", "message": ""})

        assert client._server_ready.is_set()

    def test_project_status_ok_notification(self):
        client = LSPClient(["cmd"], Path("/root"))
        assert not client._server_ready.is_set()

        client._handle_notification("language/status", {"type": "ProjectStatus", "message": "OK"})

        assert client._server_ready.is_set()

    def test_ignores_non_ready_status(self):
        client = LSPClient(["cmd"], Path("/root"))

        client._handle_notification("language/status", {"type": "Starting", "message": "loading..."})

        assert not client._server_ready.is_set()


class TestWaitForServerReady:
    def test_returns_immediately_when_already_ready(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._server_ready.set()

        # Should not block
        client.wait_for_server_ready(timeout=1)

    def test_times_out_gracefully(self):
        client = LSPClient(["cmd"], Path("/root"))

        # Should not raise, just log warning
        client.wait_for_server_ready(timeout=1)
        assert not client._server_ready.is_set()


class TestReadSingleMessage:
    def test_reads_valid_message(self):
        client = LSPClient(["cmd"], Path("/root"))
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "ok"}).encode("utf-8")
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body

        # Use a pipe to simulate stdout
        read_fd, write_fd = os.pipe()
        os.write(write_fd, raw)
        os.close(write_fd)

        client._stdout_fd = read_fd
        msg = client._read_single_message()

        assert msg is not None
        assert msg["id"] == 1
        assert msg["result"] == "ok"

        os.close(read_fd)

    def test_returns_none_for_no_fd(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._stdout_fd = None

        assert client._read_single_message() is None

    def test_returns_none_for_missing_content_length(self):
        client = LSPClient(["cmd"], Path("/root"))

        # Header without Content-Length
        raw = b"X-Custom: value\r\n\r\n"
        read_fd, write_fd = os.pipe()
        os.write(write_fd, raw)
        os.close(write_fd)

        client._stdout_fd = read_fd
        msg = client._read_single_message()

        assert msg is None
        os.close(read_fd)


class TestContextManager:
    @patch.object(LSPClient, "start")
    @patch.object(LSPClient, "shutdown")
    def test_enter_exit(self, mock_shutdown, mock_start):
        client = LSPClient(["cmd"], Path("/root"))

        with client as c:
            assert c is client

        mock_start.assert_called_once()
        mock_shutdown.assert_called_once()


class TestDidOpenDidChangeDidClose:
    def test_did_open_sends_notification(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_notification") as mock_notif:
            with patch.object(Path, "read_text", return_value="print('hi')"):
                client.did_open(Path("/root/test.py"), "python")

            mock_notif.assert_called_once()
            args = mock_notif.call_args[0]
            assert args[0] == "textDocument/didOpen"
            assert args[1]["textDocument"]["languageId"] == "python"

    def test_did_change_sends_notification(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_notification") as mock_notif:
            client.did_change(Path("/root/test.py"), "new content")

            mock_notif.assert_called_once()
            args = mock_notif.call_args[0]
            assert args[0] == "textDocument/didChange"
            assert args[1]["contentChanges"][0]["text"] == "new content"

    def test_did_change_auto_increments_version(self):
        client = LSPClient(["cmd"], Path("/root"))
        file_path = Path("/root/test.py")

        with patch.object(client, "_send_notification") as mock_notif:
            client.did_change(file_path, "v1")
            client.did_change(file_path, "v2")
            client.did_change(file_path, "v3")

            versions = [call[0][1]["textDocument"]["version"] for call in mock_notif.call_args_list]
            assert versions == [2, 3, 4]

    def test_did_change_continues_version_after_did_open(self):
        client = LSPClient(["cmd"], Path("/root"))
        file_path = Path("/root/test.py")

        with patch.object(client, "_send_notification") as mock_notif:
            with patch.object(Path, "read_text", return_value=""):
                client.did_open(file_path, "python")
            client.did_change(file_path, "edited")

            change_args = mock_notif.call_args_list[-1][0]
            assert change_args[1]["textDocument"]["version"] == 2

    def test_did_open_is_idempotent(self):
        client = LSPClient(["cmd"], Path("/root"))
        file_path = Path("/root/test.py")

        with patch.object(client, "_send_notification") as mock_notif:
            with patch.object(Path, "read_text", return_value="content"):
                client.did_open(file_path, "python")
                client.did_open(file_path, "python")

            # Only one didOpen should be sent
            assert mock_notif.call_count == 1

    def test_did_close_sends_notification(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_notification") as mock_notif:
            client.did_close(Path("/root/test.py"))

            mock_notif.assert_called_once()
            args = mock_notif.call_args[0]
            assert args[0] == "textDocument/didClose"

    def test_did_close_allows_reopen(self):
        client = LSPClient(["cmd"], Path("/root"))
        file_path = Path("/root/test.py")

        with patch.object(client, "_send_notification") as mock_notif:
            with patch.object(Path, "read_text", return_value="content"):
                client.did_open(file_path, "python")
                client.did_close(file_path)
                client.did_open(file_path, "python")

            methods = [call[0][0] for call in mock_notif.call_args_list]
            assert methods == ["textDocument/didOpen", "textDocument/didClose", "textDocument/didOpen"]

    def test_did_open_handles_unreadable_file(self):
        client = LSPClient(["cmd"], Path("/root"))

        with patch.object(client, "_send_notification") as mock_notif:
            with patch.object(Path, "read_text", side_effect=PermissionError):
                client.did_open(Path("/root/test.py"), "python")

            # Should still send with empty text
            args = mock_notif.call_args[0]
            assert args[1]["textDocument"]["text"] == ""


class TestShutdown:
    def test_shutdown_sends_requests_and_terminates(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        client._process = mock_process
        client._stdout_fd = None

        with (
            patch.object(client, "_send_request") as mock_req,
            patch.object(client, "_send_notification") as mock_notif,
        ):
            client.shutdown()

        mock_req.assert_called_once_with("shutdown", None, timeout=5)
        mock_notif.assert_called_once_with("exit", None)
        mock_process.terminate.assert_called_once()

    def test_shutdown_handles_already_dead_process(self):
        client = LSPClient(["cmd"], Path("/root"))
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Already dead
        client._process = mock_process

        # Should not raise
        client.shutdown()

    def test_shutdown_closes_stdout_fd(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = 0

        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        client._stdout_fd = read_fd

        client.shutdown()

        assert client._stdout_fd is None

    def test_shutdown_clears_document_tracking(self):
        client = LSPClient(["cmd"], Path("/root"))
        client._process = MagicMock()
        client._process.poll.return_value = 0

        # Simulate having opened a file
        uri = Path("/root/test.py").resolve().as_uri()
        client._opened_uris.add(uri)
        client._doc_versions[uri] = 3

        client.shutdown()

        assert len(client._opened_uris) == 0
        assert len(client._doc_versions) == 0
