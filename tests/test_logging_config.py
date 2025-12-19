import logging
import os
import tempfile
import unittest
from pathlib import Path

# Assuming logging_config.py exists and setup_logging is importable
from logging_config import setup_logging


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
            setup_logging(log_filename="test.log", log_dir=temp_path)

            logs_dir = temp_path / "logs"
            log_files = list(logs_dir.glob("*.log"))
            # Filter out _latest.log
            timestamped_files = [f for f in log_files if f.name != "_latest.log"]

            self.assertEqual(len(timestamped_files), 1)
            filename = timestamped_files[0].name
            # Expected format: YYYYMMDD_HHMMSS.log
            self.assertEqual(len(filename), len("YYYYMMDD_HHMMSS.log"))

            # Check _latest.log
            latest_log = logs_dir / "_latest.log"
            self.assertTrue(latest_log.exists())
            if latest_log.is_symlink():
                self.assertEqual(os.readlink(latest_log), filename)

            self._clean_logging_handlers()

    def test_setup_logging_default_filename_is_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            setup_logging(log_dir=temp_path)

            logs_dir = temp_path / "logs"
            log_files = list(logs_dir.glob("*.log"))
            # Filter out _latest.log
            timestamped_files = [f for f in log_files if f.name != "_latest.log"]

            self.assertEqual(len(timestamped_files), 1)
            self.assertEqual(len(timestamped_files[0].name), len("YYYYMMDD_HHMMSS.log"))

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
            timestamped_files = [f for f in log_files if f.name != "_latest.log"]
            self.assertEqual(len(timestamped_files), 1)

            self._clean_logging_handlers()

    def test_setup_logging_none_log_dir(self):
        # Test behavior when log_dir is None
        # We need to be careful as this might create a 'logs' folder in the current directory
        # So we'll mock or just check if it uses Path("logs")
        try:
            setup_logging(log_dir=None)
            root_logger = logging.getLogger()
            file_handler = next(h for h in root_logger.handlers if isinstance(h, logging.FileHandler))
            log_file_path = Path(file_handler.baseFilename)

            # It should be in a folder named 'logs' in the current working directory
            self.assertEqual(log_file_path.parent.name, "logs")
            self.assertEqual(log_file_path.parent.parent, Path.cwd())
        finally:
            self._clean_logging_handlers()
            # Cleanup created logs folder if it was created in CWD
            # (In a real test we'd mock Path.cwd() but for now we just verify)
            pass


if __name__ == "__main__":
    unittest.main()
