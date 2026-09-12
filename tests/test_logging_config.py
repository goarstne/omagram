from __future__ import annotations

import logging
import stat
import tempfile
import unittest
from pathlib import Path

from telegram_tui.logging_config import SecureRotatingFileHandler, configure_logging
from telegram_tui.security import UnsafePathError


class ConfigureLoggingTests(unittest.TestCase):
    def tearDown(self):
        # configure_logging replaces the root logger's handlers as a side
        # effect; make sure later tests/processes don't inherit a handler
        # pointing at a deleted temp-directory log file.
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    def test_creates_private_log_file_and_directory(self):
        with tempfile.TemporaryDirectory() as base:
            log_path = Path(base) / "state" / "omagram.log"
            result = configure_logging(log_path=str(log_path))
            self.assertEqual(result, log_path)
            self.assertTrue(log_path.is_file())
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log_path.parent.stat().st_mode), 0o700)

    def test_rejects_symlinked_log_file(self):
        with tempfile.TemporaryDirectory() as base:
            real_log = Path(base) / "real.log"
            real_log.write_text("existing contents")
            link = Path(base) / "omagram.log"
            link.symlink_to(real_log)
            with self.assertRaises(UnsafePathError):
                configure_logging(log_path=str(link))
            self.assertEqual(real_log.read_text(), "existing contents")

    def _configured_handler(self, log_path: Path) -> SecureRotatingFileHandler:
        configure_logging(log_path=str(log_path))
        return next(
            h for h in logging.getLogger().handlers
            if isinstance(h, SecureRotatingFileHandler)
        )

    def test_rollover_reopens_with_private_mode(self):
        """A normal rotation cycle must keep working and stay 0600."""
        with tempfile.TemporaryDirectory() as base:
            log_path = Path(base) / "omagram.log"
            handler = self._configured_handler(log_path)
            handler.doRollover()
            self.assertTrue(log_path.is_file())
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)

    def test_reopen_rejects_symlink_planted_after_startup(self):
        """A TOCTOU where the log path is swapped for a symlink post-startup."""
        with tempfile.TemporaryDirectory() as base:
            log_path = Path(base) / "omagram.log"
            handler = self._configured_handler(log_path)
            handler.stream.close()
            handler.stream = None
            log_path.unlink()
            evil_target = Path(base) / "evil.txt"
            evil_target.write_text("victim data")
            log_path.symlink_to(evil_target)
            with self.assertRaises(UnsafePathError):
                handler._open()
            self.assertEqual(evil_target.read_text(), "victim data")


if __name__ == "__main__":
    unittest.main()
