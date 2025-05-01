import logging
import logging.config
from pathlib import Path
import os 

def setup_logging(
    default_level: str = "INFO",
    log_filename: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5
):
    """
    Configure:
      - Console output at INFO level
      - Rotating file handler at DEBUG level writing into logs/app.log
    """
    if not os.path.exists("logs"):
        raise Exception("Logs directory not found!")
    log_path = Path("logs") / log_filename

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
                "filename": str(log_path),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
        },
        "root": {
            "level": default_level,
            "handlers": ["console", "file"],
        },
        "loggers": {
            # quiet down verbose libraries
            "git": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    }

    logging.config.dictConfig(config)
