import unittest
from types import SimpleNamespace

from telethon.tl import types

import backend


class WidgetRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_skips_archived_chats(self):
        watcher = backend.Watcher.__new__(backend.Watcher)
        watcher.contacts, watcher.selected, watcher.reload_contacts = {}, set(), False
        watcher.publish = lambda: None
        calls = []

        async def dialogs():
            yield SimpleNamespace(entity=types.User(1, first_name="Alice"), unread_count=2, message=None)

        def iter_dialogs(**kwargs):
            calls.append(kwargs)
            return dialogs()

        await watcher.refresh(SimpleNamespace(iter_dialogs=iter_dialogs))
        self.assertIs(calls[0].get("archived"), False)
        self.assertEqual(list(watcher.contacts), ["1"])


if __name__ == "__main__":
    unittest.main()
