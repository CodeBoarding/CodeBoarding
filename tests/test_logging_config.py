import codecs
import io
import logging
import logging.handlers
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Assuming logging_config.py exists and setup_logging is importable
from logging_config import add_file_handler, setup_logging


class TestLoggingConfig(unittest.TestCase):

    def _clean_logging_handlers(self):
        """Helper to close and remove all handlers from the root logger."""
        # Note: We iterate over a copy of the list (using [:] slicing)
        # because the list is modified during the loop.
        for handler in logging.root.handlers[:]:
            handler.close()
            logging.root.removeHandler(handler)

    def setUp(self):
        """Ensure a clean logger before each test."""
        # Clears any handlers that might have persisted from previous tests
        # (especially if a test failed before its teardown)
        self._clean_logging_handlers()

    def tearDown(self):
        """Ensure a clean logger after each test."""
        # Clears handlers created in the current test run
        self._clean_logging_handlers()

    def test_setup_logging_default(self):
        # Test default logging setup
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            setup_logging(log_dir=temp_path)

            # Check that logs directory was created
            logs_dir = temp_path / "logs"
            self.assertTrue(logs_dir.exists())

            self._clean_logging_handlers()

    def test_setup_logging_custom_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_filename="custom.log", log_dir=temp_path)

            logs_dir = temp_path / "logs"
            self.assertTrue(logs_dir.exists())
            self.assertTrue((logs_dir / "custom.log").exists())

            self._clean_logging_handlers()

    def test_setup_logging_custom_level(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(default_level="DEBUG", log_dir=temp_path)

            root_logger = logging.getLogger()
            self.assertEqual(root_logger.level, logging.DEBUG)

            self._clean_logging_handlers()

    def test_setup_logging_creates_logs_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            logs_dir = temp_path / "logs"
            self.assertTrue(logs_dir.exists())
            self.assertTrue(logs_dir.is_dir())

            self._clean_logging_handlers()

    def test_setup_logging_handlers_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            root_logger = logging.getLogger()
            self.assertGreaterEqual(len(root_logger.handlers), 2)

            self._clean_logging_handlers()

    def test_setup_logging_specific_loggers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            git_logger = logging.getLogger("git")
            self.assertEqual(git_logger.level, logging.WARNING)

            urllib3_logger = logging.getLogger("urllib3")
            self.assertEqual(urllib3_logger.level, logging.WARNING)

            self._clean_logging_handlers()

    def test_setup_logging_timestamped_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            logs_dir = temp_path / "logs"
            log_files = list(logs_dir.glob("*.log"))

            self.assertEqual(len(log_files), 1)
            filename = log_files[0].name
            # Expected format: YYYYMMDD_HHMMSS.log
            self.assertEqual(len(filename), len("YYYYMMDD_HHMMSS.log"))

            self._clean_logging_handlers()

    def test_setup_logging_default_filename_is_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            logs_dir = temp_path / "logs"
            log_files = list(logs_dir.glob("*.log"))

            self.assertEqual(len(log_files), 1)
            self.assertEqual(len(log_files[0].name), len("YYYYMMDD_HHMMSS.log"))

            self._clean_logging_handlers()

    def test_setup_logging_no_nested_logs_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            logs_path = temp_path / "logs"
            logs_path.mkdir()

            # Pass the already existing logs directory
            setup_logging(log_dir=logs_path)

            # Check that it didn't create logs/logs
            self.assertFalse((logs_path / "logs").exists())
            # But the log file should be inside logs_path
            log_files = list(logs_path.glob("*.log"))
            self.assertEqual(len(log_files), 1)

            self._clean_logging_handlers()

    def test_setup_logging_none_log_dir(self):
        # When log_dir is None, only a console handler should be configured
        # (no file handler, no log file created).
        setup_logging(log_dir=None)
        root_logger = logging.getLogger()
        file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(file_handlers), 0, "No file handler when log_dir is None")

        self._clean_logging_handlers()

    def test_add_file_handler_creates_new_file_each_call(self):
        """Each add_file_handler call creates a separate timestamped log file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging()

            add_file_handler(log_dir=temp_path)
            add_file_handler(log_dir=temp_path)
            count = sum(1 for h in logging.root.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
            self.assertEqual(count, 2, "Each call should add a new file handler")

            self._clean_logging_handlers()

    def test_add_file_handler_creates_log_file(self):
        """add_file_handler creates a log file in the logs subdirectory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging()

            add_file_handler(log_dir=temp_path)

            logs_dir = temp_path / "logs"
            self.assertTrue(logs_dir.exists())
            log_files = list(logs_dir.glob("*.log"))
            self.assertEqual(len(log_files), 1)

            self._clean_logging_handlers()

    def test_setup_logging_without_log_dir_is_console_only(self):
        """setup_logging() without log_dir creates only a console handler."""
        setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(file_handlers), 0, "No file handler should exist without log_dir")

        self._clean_logging_handlers()

    def test_console_handler_survives_unencodable_unicode(self):
        """Test that logging non-encodable Unicode chars doesn't crash.

        Simulates a Windows environment where sys.stdout uses a limited
        encoding (e.g. cp1251) that cannot encode characters like \u2011
        (non-breaking hyphen). Without the fix in setup_logging, logging
        would produce a "--- Logging error ---" with UnicodeEncodeError.

        Python's logging.StreamHandler.emit() catches UnicodeEncodeError
        internally and calls handleError() instead of propagating it, which
        is what produces the STDERR traceback seen in the original bug. This
        test patches handleError to convert it into a real failure.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create a stream that uses cp1251 encoding with strict error handling,
            # simulating a Windows console that can't encode \u2011.
            raw_buffer = io.BytesIO()
            strict_stream = io.TextIOWrapper(raw_buffer, encoding="cp1251", errors="strict")

            # First, verify that the strict stream CANNOT encode \u2011 directly.
            # This proves the test scenario is valid.
            with self.assertRaises(UnicodeEncodeError):
                strict_stream.write("text with non\u2011breaking hyphen")
                strict_stream.flush()

            # Now set up logging with our limited-encoding stream patched as stdout.
            # setup_logging should reconfigure it to use 'replace' error handling.
            raw_buffer = io.BytesIO()
            limited_stream = io.TextIOWrapper(raw_buffer, encoding="cp1251", errors="strict")

            with patch("sys.stdout", limited_stream):
                setup_logging(log_dir=temp_path)

            # Find the console handler that was configured with our patched stream
            console_handler = None
            for handler in logging.root.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    console_handler = handler
                    break

            self.assertIsNotNone(console_handler, "Console StreamHandler not found")
            assert console_handler is not None

            # Patch handleError on the console handler so that if emit() fails
            # internally (the "--- Logging error ---" path), we re-raise it as
            # an actual test failure instead of silently printing to stderr.
            original_handle_error = console_handler.handleError
            emit_errors: list[logging.LogRecord] = []

            def recording_handle_error(record: logging.LogRecord) -> None:
                emit_errors.append(record)
                original_handle_error(record)

            console_handler.handleError = recording_handle_error  # type: ignore[assignment]

            # Logging a message with \u2011 should succeed without triggering handleError
            logger = logging.getLogger("test.unicode")
            logger.info("static\u2011first workflow with zoom\u2011in/zoom\u2011out views")

            self.assertEqual(
                len(emit_errors),
                0,
                "StreamHandler.handleError was called — logging failed with a "
                "UnicodeEncodeError (the '--- Logging error ---' seen on Windows)",
            )

            self._clean_logging_handlers()


if __name__ == "__main__":
    unittest.main()
