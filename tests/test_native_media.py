import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual_image.widget.sixel import Image as SixelImage

from telegram_tui.app import MessagePanel, TelegramTui, terminal_image
from telegram_tui.client import Message, Dialog
from telegram_tui.native_media import sixel_widget, _ClippedSixel
from rich.control import Control


class NativeMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_foot_uses_widget(self):
        with patch.dict("os.environ", {"OMAGRAM_IMAGE_MODE": "auto"}), patch(
            "telegram_tui.app._terminal_emulator", return_value="foot"
        ):
            widget = terminal_image(Image.new("RGB", (320, 180)))
        self.assertIsInstance(widget, SixelImage)

    async def test_sixel_fits_narrow_column_and_restores_last_row(self):
        class Probe(App):
            def compose(self):
                with VerticalScroll():
                    yield sixel_widget(Image.new("RGB", (960, 540)))

        app = Probe()
        async with app.run_test(size=(24, 30)) as pilot:
            await pilot.pause()
            widget = app.query_one(SixelImage)
            self.assertLessEqual(widget.content_size.width, 24)
            self.assertEqual(widget._get_styled_size()[0], widget.content_size.width)
            child = app.query_one(_ClippedSixel)
            region = app.screen.find_widget(child).visible_region
            segments = child._get_sixel_segments("test")
            self.assertEqual(segments[-1].text, Control.move_to(region.right, region.bottom - 1).segment.text)

    async def test_newest_gif_wins_over_old_youtube_and_loops(self):
        class Probe(TelegramTui):
            async def _connect(self):
                pass

        backend = SimpleNamespace(cache_media_for_message=AsyncMock(return_value=Path("/tmp/example.mp4")))
        app = Probe(backend)
        async with app.run_test() as pilot:
            app.selected = Dialog(1, "test", 0, None)
            source = object()
            app._current_messages = [
                Message("test", "", datetime.now(), False, youtube_url="https://youtu.be/example"),
                Message("test", "", datetime.now(), False, media_kind="gif", source=source),
            ]
            with patch("telegram_tui.app.resolve_trusted_binary", return_value="/usr/bin/mpv"), patch("telegram_tui.app.subprocess.Popen") as launch:
                await app.action_play_media()
                await pilot.pause()
                backend.cache_media_for_message.assert_awaited_once_with(1, source)
                args = launch.call_args.args[0]
                self.assertIn("--loop-file=inf", args)
                self.assertEqual(args[-1], "/tmp/example.mp4")

    async def test_transcript_replacement_and_empty_chat(self):
        class Probe(App):
            def compose(self):
                with VerticalScroll():
                    yield MessagePanel(id="messages")

        app = Probe()
        async with app.run_test() as pilot:
            panel = app.query_one(MessagePanel)
            panel.write("old chat")
            await pilot.pause()
            panel.clear()
            panel.write("new chat")
            panel.write("second message")
            await pilot.pause()
            self.assertEqual(len(panel.query(Static)), 2)
            self.assertEqual(str(panel.children[0].content), "new chat")
            first = panel.children[0]
            panel.write("appended later")
            await pilot.pause()
            self.assertEqual(len(panel.children), 3)
            self.assertIs(panel.children[0], first)
            panel.clear()
            await pilot.pause()
            self.assertEqual(len(panel.children), 0)

    async def test_reload_preserves_scrolled_position(self):
        class Probe(App):
            CSS = "MessagePanel { height: auto; }"

            def compose(self):
                with VerticalScroll():
                    yield MessagePanel()

        app = Probe()
        async with app.run_test(size=(80, 20)) as pilot:
            panel = app.query_one(MessagePanel)
            for i in range(80):
                panel.write(str(i))
            await pilot.pause()
            scroll = app.query_one(VerticalScroll)
            scroll.scroll_to(y=10, animate=False)
            await pilot.pause()
            panel.clear()
            for i in range(80):
                panel.write(f"updated {i}")
            await pilot.pause()
            self.assertEqual(scroll.scroll_y, 10)


if __name__ == "__main__":
    unittest.main()
