"""Explicit live check; reads an existing chat and plays its latest GIF.

Run from the repo: PYTHONPATH=. uv run python tests/live_media_check.py CHAT_ID
Uses the existing account session. Does not send messages or mark them read.
"""
import asyncio
import logging
import sys

from telegram_tui.app import TelegramTui
from telegram_tui.client import TelegramBackend
from telegram_tui.config import SESSION_PATH, load_config
from telegram_tui.logging_config import configure_logging


class LiveMediaCheck(TelegramTui):
    async def _connect(self):
        await super()._connect()
        dialog = next(d for d in self.backend.dialogs if d.id == int(sys.argv[1]))
        self.selected = dialog
        self._update_chat_title(dialog)
        await self._load_messages(dialog)
        media = next(m for m in reversed(self._current_messages) if m.media_kind == "gif")
        path, _, _ = await self.backend.load_message_preview(dialog.id, media)
        media.media_path = path
        # Show the selected GIF using the production transcript and renderer.
        self._render_messages([media])
        logging.getLogger("omagram.check").info("native GIF preview ready")
        self._set_status("GIF preview check · playback starts in 10 seconds")
        self.set_timer(10, self.action_play_media)


async def main():
    configure_logging(debug=True, log_path="/tmp/omagram-live-media-check.log")
    backend = TelegramBackend(*load_config(), str(SESSION_PATH))
    try:
        await asyncio.wait_for(backend.connect(), 20)
        await LiveMediaCheck(backend).run_async()
    finally:
        await backend.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
