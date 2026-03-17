import io
import logging
import logging.config
from datetime import datetime
from pathlib import Path


def setup_logging(
    default_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    log_dir: Path | None = None,
    log_filename: str | None = None,
):
    """
    Configure:
      - Console output at INFO level
      - Rotating file handler at DEBUG level writing into timestamped log files in logs directory
    """
    # Define both handlers from the beginning
    handlers = ["console", "file"]

    # Determine log file location
    if log_dir is None:
        logs_dir = Path("logs")
    else:
        # If log_dir is provided, put logs in a 'logs' subdirectory
        # unless log_dir itself is already named 'logs'
        if log_dir.name == "logs":
            logs_dir = log_dir
        else:
            logs_dir = log_dir / "logs"

    # Create logs directory if it doesn't exist
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    if log_filename:
        filename = log_filename
    else:
        # Generate timestamped filename for per-run logs
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.log"

    # Create full path for log file
    log_file_path = logs_dir / filename

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

    # Reconfigure the console handler's stream to use 'replace' error handling
    # so that non-encodable Unicode characters (e.g. \u2011 on Windows cp1251)
    # don't crash the logging system.
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            stream = handler.stream
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
            elif hasattr(stream, "encoding") and stream.encoding and stream.encoding.lower() != "utf-8":
                handler.stream = io.TextIOWrapper(
                    stream.buffer, encoding=stream.encoding, errors="replace", line_buffering=stream.line_buffering
                )
