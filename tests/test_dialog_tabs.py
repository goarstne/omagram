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
            ("groups", "Groups"), ("archived", "Archived"), ("folder:7", "Work"),
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

    def test_archived_chats_only_appear_in_archived_view(self):
        backend = object.__new__(TelegramBackend)
        dialogs = [
            Dialog(1, "Alice", 0, object(), is_private=True),
            Dialog(2, "Spam", 0, object(), is_private=True, is_archived=True),
            Dialog(3, "Team", 0, object(), is_group=True),
            Dialog(4, "Old Team", 0, object(), is_group=True, is_archived=True),
        ]
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "all")], [1, 3])
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "private")], [1])
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "groups")], [3])
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "archived")], [2, 4])

    async def test_load_dialogs_requests_matching_telegram_folder(self):
        backend = object.__new__(TelegramBackend)
        backend._custom_filters = {"folder:7": types.DialogFilter(
            7, types.TextWithEntities("Work", []), [], [], [], groups=True
        )}
        calls = []

        async def no_dialogs():
            return
            yield

        def iter_dialogs(**kwargs):
            calls.append(kwargs.get("archived"))
            return no_dialogs()

        backend.client = SimpleNamespace(is_connected=lambda: True, iter_dialogs=iter_dialogs)
        for tab in ("all", "private", "groups", "archived", "folder:7"):
            await backend.load_dialogs(tab=tab)
        self.assertEqual(calls, [False, False, False, True, None])


if __name__ == "__main__":
    unittest.main()
