from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
import platform
import sys

from .security import reject_symlink, secure_state_dir


APP_STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
) / "omagram"
DEFAULT_LOG_PATH = APP_STATE_DIR / "omagram.log"
LOGGER_NAME = "omagram"


class SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A RotatingFileHandler that re-validates the path on every (re)open.

    The base class reopens ``baseFilename`` with a plain ``open()`` call
    after every rollover, with no symlink protection. A one-time check at
    startup does not cover that: an attacker who replaces the log path with
    a symlink sometime after startup — but before the next rotation — would
    otherwise have writes silently redirected (or an unrelated file
    truncated/appended to) the moment ``doRollover()`` reopens the stream.
    """

    def _open(self):
        path = Path(self.baseFilename)
        secure_state_dir(path.parent)
        reject_symlink(path)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | nofollow, 0o600)
        os.close(fd)
        return super()._open()


def configure_logging(
    *,
    debug: bool = False,
    log_path: str | os.PathLike[str] | None = None,
    console: bool = False,
) -> Path:
    """Configure safe rotating diagnostics before any app code starts."""
    destination = Path(log_path or os.environ.get("OMAGRAM_LOG_FILE", DEFAULT_LOG_PATH))
    # The directory is also (re-)validated inside SecureRotatingFileHandler
    # on every open/rollover below; this early check just fails fast with a
    # clean stack before any logging machinery is touched.
    secure_state_dir(destination.parent)
    reject_symlink(destination)

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

    file_handler = SecureRotatingFileHandler(
        destination,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
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
