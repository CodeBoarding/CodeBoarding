import logging
import logging.config
from pathlib import Path


def setup_logging(
    default_level: str = "INFO",
    log_filename: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    log_dir: Path | None = None,
):
    """
    Configure:
      - Console output at INFO level
      - Rotating file handler at DEBUG level writing into logs/app.log
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

    # Create full path for log file
    log_file_path = logs_dir / log_filename

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
