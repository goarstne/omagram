"""Render showcase screenshots with fake, non-real chat data.

Drives the real TelegramTui app against a stub backend (no network, no
Telegram account) so screenshots/*.svg can be committed and pushed to
GitHub without exposing any real conversations. Regenerate after UI
changes with:

    uv run python scripts/demo_screenshots.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_tui.app import TelegramTui
from telegram_tui.client import Dialog, Message

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"

NOW = datetime(2026, 9, 11, 18, 42, tzinfo=timezone.utc)


class _FakeClient:
    def is_connected(self) -> bool:
        return True


class _FakeBackend:
    """Just enough of TelegramBackend's surface for the UI to render."""

    def __init__(self, dialogs: list[Dialog], messages: dict[int, list[Message]]):
        self.client = _FakeClient()
        self.dialogs = dialogs
        self._messages = messages
        self.on_new_message = None

    async def connect(self) -> None:
        return None

    async def authorized(self) -> bool:
        return True

    async def load_dialogs(self) -> list[Dialog]:
        return self.dialogs

    async def messages(self, dialog: Dialog) -> list[Message]:
        return self._messages.get(dialog.id, [])

    async def disconnect(self) -> None:
        return None


def _dialogs() -> list[Dialog]:
    return [
        Dialog(id=1, title="Night Owls // BBS Ops", unread=3, entity=None,
               preview="carrier signal locked, who's up for a raid tonight?"),
        Dialog(id=2, title="retro-82 gang", unread=0, entity=None,
               preview="new wallpaper drop, check the neon set"),
        Dialog(id=3, title="Modem Pool", unread=12, entity=None,
               preview="anyone still got a working 14.4k?"),
        Dialog(id=4, title="Ada", unread=0, entity=None,
               preview="sending the demo disk over sneakernet"),
        Dialog(id=5, title="Keyboard Warriors", unread=1, entity=None,
               preview="j/k gang rise up"),
        Dialog(id=6, title="Omarchy Users", unread=0, entity=None,
               preview="theme-set hook finally works on foot"),
        Dialog(id=7, title="Pixel Basement", unread=0, entity=None,
               preview="sixel > everything, fight me"),
        Dialog(id=8, title="Dial-Up Diaries", unread=7, entity=None,
               preview="NO CARRIER again..."),
    ]


def _messages_for_dialog_one() -> list[Message]:
    yesterday = NOW - timedelta(days=1)
    return [
        Message(
            sender="Rae", text="carrier signal locked, who's up for a raid tonight?",
            timestamp=yesterday.replace(hour=21, minute=10), outgoing=False,
        ),
        Message(
            sender="you", text="always. what time?",
            timestamp=yesterday.replace(hour=21, minute=12), outgoing=True,
        ),
        Message(
            sender="you", text="terminal's already warmed up",
            timestamp=yesterday.replace(hour=21, minute=12, second=40), outgoing=True,
        ),
        Message(
            sender="Rae", text="22:00 sharp, usual channel",
            timestamp=NOW.replace(hour=9, minute=4), outgoing=False,
        ),
        Message(
            sender="Rae", text="bring the good mod files this time",
            timestamp=NOW.replace(hour=9, minute=5), outgoing=False,
        ),
        Message(
            sender="you", text="",
            timestamp=NOW.replace(hour=9, minute=6), outgoing=True,
            media_kind="file", media_name="omagram-flyer.png", media_size=182_300,
        ),
        Message(
            sender="Rae", text="",
            timestamp=NOW.replace(hour=9, minute=8), outgoing=False,
            media_kind="gif", media_name="dance.gif",
        ),
        Message(
            sender="Rae", text="lol perfect",
            timestamp=NOW.replace(hour=9, minute=8, second=20), outgoing=False,
        ),
        Message(
            sender="you", text="see you at 22:00 ›",
            timestamp=NOW.replace(hour=18, minute=40), outgoing=True,
        ),
    ]


async def _render() -> None:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    dialogs = _dialogs()
    backend = _FakeBackend(dialogs, {1: _messages_for_dialog_one()})
    app = TelegramTui(backend)

    async with app.run_test(size=(104, 40)) as pilot:
        await pilot.pause(0.3)
        await app._reload_dialogs()
        view = app.query_one("#dialogs")
        view.index = 0
        app.selected = dialogs[0]
        app._update_chat_title(app.selected)
        await app._load_messages(app.selected)
        app._set_status("Online · j/k navigate · Enter open · c write")
        await pilot.pause(0.3)

        svg = app.export_screenshot(title="omagram — Telegram · Omarchy")
        (SCREENSHOTS_DIR / "chat-view.svg").write_text(svg)
        print(f"wrote {SCREENSHOTS_DIR / 'chat-view.svg'}")


if __name__ == "__main__":
    asyncio.run(_render())
