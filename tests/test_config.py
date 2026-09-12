from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_tui.config import (
    DEFAULT_API_ID,
    DEFAULT_API_HASH,
    load_config,
    save_config,
    validate_session_path,
)
from telegram_tui.security import UnsafePathError


class ConfigTests(unittest.TestCase):
    def test_default_fallback_when_env_empty(self):
        with patch.dict(os.environ, {"TG_API_ID": "", "TG_API_HASH": ""}, clear=False), \
             patch("telegram_tui.config.load_dotenv"):
            api_id, api_hash = load_config()
            self.assertEqual(api_id, DEFAULT_API_ID)
            self.assertEqual(api_hash, DEFAULT_API_HASH)

    def test_custom_credentials_used_when_set(self):
        with patch.dict(os.environ, {"TG_API_ID": "123456", "TG_API_HASH": "customhash"}, clear=False), \
             patch("telegram_tui.config.load_dotenv"):
            api_id, api_hash = load_config()
            self.assertEqual(api_id, 123456)
            self.assertEqual(api_hash, "customhash")

    def test_invalid_api_id_raises(self):
        with patch.dict(os.environ, {"TG_API_ID": "not-a-number", "TG_API_HASH": "customhash"}, clear=False), \
             patch("telegram_tui.config.load_dotenv"):
            with self.assertRaises(RuntimeError):
                load_config()


class SaveConfigTests(unittest.TestCase):
    def test_writes_env_file_atomically_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as state:
            with patch("pathlib.Path.home", return_value=Path(home)), patch(
                "telegram_tui.config.APP_DIR", Path(state) / "omagram"
            ):
                save_config("123456", "somehash")
            env_path = Path(home) / ".config/omagram/.env"
            self.assertTrue(env_path.is_file())
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
            self.assertEqual(
                env_path.read_text(), "TG_API_ID=123456\nTG_API_HASH=somehash\n"
            )
            self.assertEqual(
                stat.S_IMODE(env_path.parent.stat().st_mode), 0o700
            )

    def test_refuses_to_overwrite_symlinked_env_file(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as state:
            config_dir = Path(home) / ".config/omagram"
            config_dir.mkdir(parents=True, mode=0o700)
            real_target = Path(home) / "elsewhere.txt"
            real_target.write_text("do not touch")
            (config_dir / ".env").symlink_to(real_target)
            with patch("pathlib.Path.home", return_value=Path(home)), patch(
                "telegram_tui.config.APP_DIR", Path(state) / "omagram"
            ):
                with self.assertRaises(UnsafePathError):
                    save_config("1", "h")
            self.assertEqual(real_target.read_text(), "do not touch")


class ValidateSessionPathTests(unittest.TestCase):
    def test_creates_private_parent_directory(self):
        with tempfile.TemporaryDirectory() as base:
            session_path = Path(base) / "nested" / "telegram"
            result = validate_session_path(session_path)
            self.assertEqual(result, session_path)
            self.assertEqual(stat.S_IMODE(session_path.parent.stat().st_mode), 0o700)

    def test_rejects_symlinked_session_file(self):
        with tempfile.TemporaryDirectory() as base:
            real_session = Path(base) / "real.session"
            real_session.write_text("session-data")
            link = Path(base) / "telegram"
            link.symlink_to(real_session)
            with self.assertRaises(UnsafePathError):
                validate_session_path(link)


if __name__ == "__main__":
    unittest.main()
