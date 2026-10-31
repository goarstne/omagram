import ast
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from textual import events
from textual.widgets import Input

from telegram_tui.app import GifScreen, SelectableText, TelegramTui
from telegram_tui.client import Dialog, Message


class OfflineApp(TelegramTui):
    async def _connect(self):
        pass


def backend():
    return SimpleNamespace(dialogs=[], dialog_tabs=[])


def gif_backend():
    dialogs = [Dialog(1, "Alice", 0, object())]
    return SimpleNamespace(
        dialogs=dialogs,
        dialog_tabs=[],
        load_dialogs=AsyncMock(return_value=dialogs),
        recent_gifs=AsyncMock(return_value=[SimpleNamespace(name="party.gif")]),
        send_gif=AsyncMock(),
    )


class AppRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_selection_is_copied_on_release(self):
        app = OfflineApp(backend())
        async with app.run_test() as pilot:
            selection = SelectableText("abcdef", "abcdef")
            await app.mount(selection)
            await pilot.pause()
            selection.text_select_all()
            with patch.object(app, "_copy_to_clipboard", return_value=True) as copy:
                app.screen.post_message(events.TextSelected())
                await pilot.pause()
            copy.assert_called_once_with("abcdef")

    async def test_super_v_with_chat_list_focused_pastes_into_composer(self):
        app = OfflineApp(backend())
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Input)
            composer.value = "draft"
            app.query_one("#dialogs").focus()
            await pilot.pause()
            app.post_message(events.Paste(" + pasted"))
            await pilot.pause()
            self.assertIs(app.focused, composer)
            self.assertEqual(composer.value, "draft + pasted")
            # Nothing left highlighted for the next keystroke to overwrite.
            self.assertTrue(composer.selection.is_empty)

    async def test_multiline_paste_keeps_every_line(self):
        app = OfflineApp(backend())
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Input)
            composer.focus()
            await pilot.pause()
            app.post_message(events.Paste("\nfirst line\nsecond line\n"))
            await pilot.pause()
            self.assertEqual(composer.value, "first line second line")

    async def test_ctrl_v_in_composer_reads_system_clipboard(self):
        app = OfflineApp(backend())
        async with app.run_test() as pilot:
            app.query_one("#composer", Input).focus()
            with patch.object(app, "read_system_clipboard", return_value="from wayland"):
                await pilot.press("ctrl+v")
            self.assertEqual(app.query_one("#composer", Input).value, "from wayland")

    async def test_ctrl_insert_copies_composer_selection(self):
        app = OfflineApp(backend())
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", Input)
            composer.focus()
            composer.value = "draft text"
            composer.select_all()
            with patch.object(app, "_copy_to_clipboard", return_value=True) as copy:
                await pilot.press("ctrl+insert")
            copy.assert_called_once_with("draft text")

    def test_missing_clipboard_binary_is_handled(self):
        with patch("telegram_tui.app.resolve_trusted_binary", return_value=None), patch(
            "telegram_tui.app.subprocess.run"
        ) as run:
            self.assertFalse(TelegramTui._copy_to_clipboard(SimpleNamespace(), "copied"))
        run.assert_not_called()

    async def test_copy_falls_back_to_osc52_without_wl_copy(self):
        app = OfflineApp(backend())
        async with app.run_test():
            with patch.object(app, "_copy_to_clipboard", return_value=False):
                app.copy_to_clipboard("fallback")
            self.assertEqual(app.clipboard, "fallback")

    def test_urls_keep_balanced_parentheses_but_strip_extra_closing_one(self):
        balanced = "https://example.com/wiki/Dog_(animal)"
        self.assertEqual(TelegramTui._extract_urls(f"See {balanced}."), [balanced])
        self.assertIn(f'[link="{balanced}"]{balanced}[/link].', TelegramTui._format_links(f"See {balanced}."))
        self.assertEqual(TelegramTui._extract_urls("See https://example.com/path)."), ["https://example.com/path"])

    def test_every_exclusive_worker_has_a_group(self):
        tree = ast.parse(Path("telegram_tui/app.py").read_text())
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "run_worker":
                continue
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if isinstance(keywords.get("exclusive"), ast.Constant) and keywords["exclusive"].value is True:
                self.assertIn("group", keywords)

    async def test_pressing_g_opens_gif_picker_without_leaking_character_into_composer(self):
        # Input's own _on_key still runs after ComposerInput.on_key's
        # event.stop() unless prevent_default() is also called, so a bare
        # stop() used to leave a literal "g" typed into the composer.
        api = gif_backend()
        app = OfflineApp(api)
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            composer = app.query_one("#composer", Input)
            composer.focus()
            await pilot.press("g")
            await pilot.pause()
            self.assertEqual(composer.value, "")
            self.assertIsInstance(app.screen, GifScreen)

    async def test_concurrent_gif_picker_opens_do_not_stack_two_screens(self):
        # A doubled "g" keypress (key-repeat/bounce) used to race two
        # _open_gif_picker() calls: both saw an empty screen_stack before
        # either pushed, stacking two GifScreens. One Enter then dismissed
        # only the top one, leaving a second identical-looking screen
        # behind and reading as an auto-sent GIF.
        api = gif_backend()
        app = OfflineApp(api)
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            await asyncio.gather(app._open_gif_picker(), app._open_gif_picker())
            await pilot.pause()
            gif_screens = [screen for screen in app.screen_stack if isinstance(screen, GifScreen)]
            self.assertEqual(len(gif_screens), 1)
            await pilot.press("enter")
            await pilot.pause()
            api.send_gif.assert_awaited_once()
            self.assertNotIsInstance(app.screen, GifScreen)

    async def test_reloading_same_dialog_does_not_cancel_in_flight_media_hydration(self):
        # A busy chat reloads on every new message (_after_send /
        # _refresh_live_message). Before this fix, every _load_messages()
        # call for the same dialog shared one exclusive hydration group,
        # so a second reload cancelled the first pass's still-downloading
        # GIF preview before it ever finished and got cached -- previews
        # in an active chat never had a chance to complete.
        dialogs = [Dialog(1, "Alice", 0, object())]
        first_call_started = asyncio.Event()
        release_first_call = asyncio.Event()
        first_call_finished = asyncio.Event()
        calls: list[object] = []

        async def load_message_preview(dialog_id, message, enabled):
            calls.append(message)
            if len(calls) == 1:
                first_call_started.set()
                await release_first_call.wait()
                first_call_finished.set()
            return Path("/tmp/preview.jpg"), None, None

        def make_message() -> Message:
            return Message("Alice", "", datetime.now(), False, media_kind="gif", source=object())

        api = SimpleNamespace(
            dialogs=dialogs,
            dialog_tabs=[],
            messages=AsyncMock(side_effect=lambda dialog: [make_message()]),
            load_message_preview=load_message_preview,
            load_dialogs=AsyncMock(return_value=dialogs),
        )
        app = OfflineApp(api)
        async with app.run_test() as pilot:
            app.selected = dialogs[0]
            await app._load_messages(dialogs[0])
            await asyncio.wait_for(first_call_started.wait(), timeout=2)
            # Reload the same dialog again while generation 1's preview
            # download is still in flight.
            await app._load_messages(dialogs[0])
            await pilot.pause()
            release_first_call.set()
            await asyncio.wait_for(first_call_finished.wait(), timeout=2)
            await pilot.pause()
            self.assertGreaterEqual(len(calls), 2)
