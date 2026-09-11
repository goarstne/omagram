from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from telegram_tui.config import DEFAULT_API_ID, DEFAULT_API_HASH, load_config


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


if __name__ == "__main__":
    unittest.main()
