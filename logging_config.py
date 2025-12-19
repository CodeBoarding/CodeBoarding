import logging
import logging.config
import os
import shutil
from datetime import datetime
from pathlib import Path


def setup_logging(
    default_level: str = "INFO",
    log_filename: str = "codeboarding.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    log_dir: Path | None = None,
):
    """
    Configure:
      - Console output at INFO level
      - Rotating file handler at DEBUG level writing into timestamped log files in logs directory
      - Updates a '_latest.log' symlink to point to the current log file
    """
    # Define both handlers from the beginning
    handlers = ["console", "file"]

    # Determine log file location - use provided log_dir or default to current directory
    if log_dir is None:
        log_dir_path = Path(".")
    else:
        log_dir_path = Path(log_dir)

    logs_dir = log_dir_path / "logs"

    # Create logs directory if it doesn't exist
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamped filename for per-run logs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped_filename = f"{timestamp}.log"

    # Create full path for log file
    log_file_path = logs_dir / timestamped_filename

    # Basic config structure with both handlers defined upfront
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": default_level,
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "DEBUG",
                "formatter": "standard",
                "filename": str(log_file_path),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
        },
        "root": {
            "level": default_level,
            "handlers": handlers,
        },
        "loggers": {
            # quiet down verbose libraries
            "git": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    }

    logging.config.dictConfig(config)

    # Handle _latest.log symlink
    latest_log_path = logs_dir / "_latest.log"
    try:
        if latest_log_path.exists() or latest_log_path.is_symlink():
            latest_log_path.unlink()

        # Try to create a symlink (works on Unix and Windows with Developer Mode)
        # Use relative path for portability
        os.symlink(timestamped_filename, latest_log_path)
    except (OSError, AttributeError):
        # Fallback to copying the file if symlinking fails
        try:
            shutil.copy2(log_file_path, latest_log_path)
        except Exception:
            # We don't want to crash the whole app if _latest.log fails
            pass
