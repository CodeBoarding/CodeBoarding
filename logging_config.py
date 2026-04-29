import io
import logging
import logging.config
import logging.handlers
from datetime import datetime
from pathlib import Path

_LOG_FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging(
    default_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    log_dir: Path | None = None,
    log_filename: str | None = None,
):
    """Configure logging.

    When *log_dir* is provided the file handler is created immediately
    (backwards-compatible behaviour).  When *log_dir* is ``None`` only the
    console handler is configured; call :func:`add_file_handler` later to
    attach the file handler once the output directory is known.
    """
    handlers = ["console"]

    config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": _LOG_FORMATTER._fmt,
                "datefmt": _LOG_FORMATTER.datefmt,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": default_level,
                "formatter": "standard",
                "stream": "ext://sys.stderr",
            },
        },
        "root": {
            "level": default_level,
            "handlers": handlers,
        },
        "loggers": {
            "git": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    }

    if log_dir is not None:
        log_file_path = _resolve_log_path(log_dir, log_filename)
        config["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "standard",
            "filename": str(log_file_path),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
        }
        handlers.append("file")

    logging.config.dictConfig(config)
    _fix_console_encoding()


def add_file_handler(
    log_dir: Path,
    log_filename: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Attach a rotating file handler to the root logger.

    Call this once the output directory is known (e.g. right before analysis
    starts) so that no log file is created during early session initialization.
    Each call creates a new timestamped log file.
    """
    log_file_path = _resolve_log_path(log_dir, log_filename)
    handler = logging.handlers.RotatingFileHandler(
        str(log_file_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_LOG_FORMATTER)
    logging.root.addHandler(handler)


def _resolve_log_path(log_dir: Path, log_filename: str | None) -> Path:
    """Determine log file path and ensure the directory exists."""
    if log_dir.name == "logs":
        logs_dir = log_dir
    else:
        logs_dir = log_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if log_filename:
        filename = log_filename
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.log"

    path = logs_dir / filename
    if path.exists():
        stem = path.stem
        suffix = path.suffix
        counter = 2
        while path.exists():
            path = logs_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    return path


def _fix_console_encoding() -> None:
    """Reconfigure console handler to use 'replace' error handling.

    Prevents UnicodeEncodeError on Windows consoles with limited encodings.
    """
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            stream = handler.stream
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
            elif hasattr(stream, "encoding") and stream.encoding and stream.encoding.lower() != "utf-8":
                handler.stream = io.TextIOWrapper(
                    stream.buffer, encoding=stream.encoding, errors="replace", line_buffering=stream.line_buffering
                )
