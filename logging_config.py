import contextlib
import io
import logging
import logging.config
import os
import shutil
from datetime import datetime
from pathlib import Path


BROKEN_STDOUT_WARNING = (
    "Console logging disabled because stdout has no active reader. "
    "This usually indicates a stale server or broken stdio pipe from a previous debug session."
)


def _build_logging_config(
    default_level: str,
    log_file_path: Path,
    max_bytes: int,
    backup_count: int,
    include_console: bool = True,
) -> dict:
    handlers = ["file"]
    handler_config = {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "standard",
            "filename": str(log_file_path),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
        },
    }
    if include_console:
        handlers.insert(0, "console")
        handler_config["console"] = {
            "class": "logging.StreamHandler",
            "level": default_level,
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": handler_config,
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


def _clear_existing_handlers_safely() -> None:
    loggers = [logging.root]
    loggers.extend(logger for logger in logging.root.manager.loggerDict.values() if isinstance(logger, logging.Logger))

    for logger in loggers:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                handler.flush()
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                handler.close()

    logging._handlers.clear()
    del logging._handlerList[:]


def _reconfigure_console_stream_handlers() -> None:
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
      - Updates a '_latest.log' symlink to point to the current log file
    """
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

    config = _build_logging_config(default_level, log_file_path, max_bytes, backup_count)

    try:
        logging.config.dictConfig(config)
    except BrokenPipeError:
        _clear_existing_handlers_safely()
        logging.config.dictConfig(_build_logging_config(default_level, log_file_path, max_bytes, backup_count, False))
        logging.getLogger(__name__).warning(BROKEN_STDOUT_WARNING)

    _reconfigure_console_stream_handlers()

    # Handle _latest.log symlink
    latest_log_path = logs_dir / "_latest.log"
    try:
        if latest_log_path.exists() or latest_log_path.is_symlink():
            latest_log_path.unlink()

        # Try to create a symlink (works on Unix and Windows with Developer Mode)
        # Use relative path for portability
        os.symlink(filename, latest_log_path)
    except (OSError, AttributeError):
        # Fallback to copying the file if symlinking fails
        try:
            shutil.copy2(log_file_path, latest_log_path)
        except Exception:
            # We don't want to crash the whole app if _latest.log fails
            pass
