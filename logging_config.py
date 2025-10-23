import logging
import logging.config
import os
from pathlib import Path
from typing import Optional

def setup_logging(
    default_level: str = "INFO",
    log_filename: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024, # 10 MB
    backup_count: int = 5,
    log_dir: Optional[Path] = None,
):
    """
    Configures logging with console and rotating file handlers.

    The console handler outputs messages at an INFO level by default, which can be
    overridden by the `LOG_LEVEL` environment variable. The file handler captures
    all messages at the DEBUG level and above.

    :param default_level: The default logging level for the console handler.
                          Defaults to "INFO".
    :param log_filename: The name of the log file. Defaults to "app.log".
    :param max_bytes: The maximum size of a log file before it rotates.
                      Defaults to 10 MB.
    :param backup_count: The number of backup log files to keep. Defaults to 5.
    :param log_dir: The directory to store log files in. Defaults to a "logs"
                    folder in the current working directory.
    """
    # Allow log level to be set by an environment variable for flexibility
    console_log_level = os.getenv("LOG_LEVEL", default_level).upper()

    # If log_dir is not provided, default to a 'logs' subdirectory in the CWD
    if log_dir is None:
        log_dir = Path.cwd() / "logs"
    
    # Ensure the log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / log_filename

    logging_config = {
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
                "level": console_log_level,
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
            }
        },
        "root": {
            # Set root level to the most verbose handler to capture all messages
            "level": "DEBUG",
            "handlers": ["console", "file"],
        },
        "loggers": {
            # Quiet down noisy third-party libraries
            "git": {"level": "WARNING", "propagate": True},
            "urllib3": {"level": "WARNING", "propagate": True},
        },
    }

    logging.config.dictConfig(logging_config)
