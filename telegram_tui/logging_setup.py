from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
import platform
import sys


APP_STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
) / "omagram"
DEFAULT_LOG_PATH = APP_STATE_DIR / "omagram.log"
LOGGER_NAME = "omagram"


def configure_logging(
    *,
    debug: bool = False,
    log_path: str | os.PathLike[str] | None = None,
    console: bool = False,
) -> Path:
    """Configure safe rotating diagnostics before any app code starts."""
    destination = Path(log_path or os.environ.get("OMAGRAM_LOG_FILE", DEFAULT_LOG_PATH))
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    requested_level = os.environ.get("OMAGRAM_LOG_LEVEL", "DEBUG" if debug else "INFO").upper()
    level = getattr(logging, requested_level, logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s [pid=%(process)d task=%(task)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    class TaskFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                record.task = asyncio.current_task()
                record.task = record.task.get_name() if record.task else "-"
            except RuntimeError:
                record.task = "-"
            return True

    file_handler = logging.handlers.RotatingFileHandler(
        destination,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TaskFilter())

    handlers: list[logging.Handler] = [file_handler]
    if console or os.environ.get("OMAGRAM_LOG_CONSOLE") == "1":
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(TaskFilter())
        handlers.append(stream_handler)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in handlers:
        root.addHandler(handler)

    # Telethon can be extremely verbose. Keep protocol internals quiet unless
    # explicitly requested, while always retaining Omagram's own diagnostics.
    telethon_level = logging.DEBUG if debug or os.environ.get("OMAGRAM_LOG_TELETHON") == "1" else logging.WARNING
    logging.getLogger("telethon").setLevel(telethon_level)
    logging.captureWarnings(True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.info(
        "logging configured path=%s level=%s debug=%s console=%s python=%s platform=%s cwd=%s",
        destination,
        logging.getLevelName(level),
        debug,
        len(handlers) > 1,
        platform.python_version(),
        platform.platform(),
        Path.cwd(),
    )
    return destination


def install_exception_logging() -> None:
    """Log uncaught synchronous and asyncio exceptions with tracebacks."""
    logger = logging.getLogger(LOGGER_NAME)
    previous_hook = sys.excepthook

    def excepthook(exc_type: type[BaseException], value: BaseException, traceback: object) -> None:
        logger.critical("uncaught exception", exc_info=(exc_type, value, traceback))
        previous_hook(exc_type, value, traceback)

    sys.excepthook = excepthook


def install_asyncio_exception_logging(loop: asyncio.AbstractEventLoop) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    previous_handler = loop.get_exception_handler()

    def exception_handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        exception = context.get("exception")
        exc_info = (
            (type(exception), exception, exception.__traceback__)
            if isinstance(exception, BaseException)
            else None
        )
        logger.error(
            "asyncio unhandled exception message=%s exception=%r future=%r task=%r",
            context.get("message"),
            context.get("exception"),
            context.get("future"),
            context.get("task"),
            exc_info=exc_info,
        )
        if previous_handler:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(exception_handler)


def log_file_path() -> Path:
    return Path(os.environ.get("OMAGRAM_LOG_FILE", DEFAULT_LOG_PATH))
