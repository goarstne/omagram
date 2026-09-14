import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telethon.tl import types

from telegram_tui.client import Dialog, DialogTab, TelegramBackend


class DialogTabTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_user_folders_alongside_private_and_groups(self):
        backend = object.__new__(TelegramBackend)
        folder = types.DialogFilter(
            7, types.TextWithEntities("Work", []), [], [], [], groups=True
        )
        backend.client = AsyncMock(return_value=[folder])
        tabs = await backend.load_dialog_tabs()
        self.assertEqual([(tab.key, tab.title) for tab in tabs], [
            ("all", "Chats"), ("private", "Private"),
            ("groups", "Groups"), ("folder:7", "Work"),
        ])

    async def test_accepts_telethon_dialog_filters_response(self):
        backend = object.__new__(TelegramBackend)
        folder = types.DialogFilter(
            9, types.TextWithEntities("Archive", []), [], [], [], groups=True
        )
        backend.client = AsyncMock(return_value=SimpleNamespace(filters=[folder]))
        tabs = await backend.load_dialog_tabs()
        self.assertEqual(tabs[-1].title, "Archive")

    def test_builtin_views_filter_dialogs(self):
        backend = object.__new__(TelegramBackend)
        dialogs = [
            Dialog(1, "Alice", 0, object(), is_private=True),
            Dialog(2, "Team", 0, object(), is_group=True),
            Dialog(3, "News", 0, object(), is_channel=True),
        ]
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "private")], [1])
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "groups")], [2])
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "all")], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
