import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from rich.text import Text
from textual.widgets import Button, Input, ListView, Static

from telegram_tui.app import (
    ChatListView, DialogItem, MediaScreen, MessagePanel, TelegramTui,
    UploadScreen, media_label, terminal_image,
)
from telegram_tui.client import Dialog, Message
from textual_image.widget import HalfcellImage, TGPImage


class OfflineApp(TelegramTui):
    async def _connect(self):
        pass


def backend():
    dialogs = [Dialog(1, 'Alice', 0, object()), Dialog(2, 'Bob', 0, object())]
    return SimpleNamespace(
        dialogs=dialogs, send_file=AsyncMock(), send_message=AsyncMock(),
        messages=AsyncMock(return_value=[]), load_dialogs=AsyncMock(return_value=dialogs),
        cache_media_for_message=AsyncMock(return_value=Path('/tmp/media.mp4')),
    )


class AppMediaUploadTests(unittest.IsolatedAsyncioTestCase):
    def test_urls_are_emitted_as_clickable_links_and_punctuation_stays_plain(self):
        formatted = TelegramTui._format_links("See https://example.com/a?q=1&x=2, then <ok>")
        self.assertIn('[link="https://example.com/a?q=1&x=2"]https://example.com/a?q=1&x=2[/link],', formatted)
        self.assertIn("<ok>", formatted)
        rendered = Text.from_markup(formatted)
        self.assertTrue(any(span.style == 'link "https://example.com/a?q=1&x=2"' for span in rendered.spans))

    async def test_upload_explicit_send_progress_chat_change_and_success(self):
        api = backend()
        started, finish = asyncio.Event(), asyncio.Event()

        async def send(dialog, path, caption='', progress_callback=None):
            progress_callback(1, 2)
            started.set()
            await finish.wait()

        api.send_file.side_effect = send
        app = OfflineApp(api)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.txt'
            path.write_text('hi')
            async with app.run_test(size=(80, 28)) as pilot:
                app.selected = api.dialogs[0]
                await pilot.press('s')
                screen = app.screen
                self.assertIsInstance(screen, UploadScreen)
                screen.query_one('#upload-path', Input).value = str(path)
                screen.query_one('#upload-caption', Input).value = 'caption [literal]'
                await pilot.press('enter')
                api.send_file.assert_not_awaited()
                await pilot.click('#upload-send')
                await asyncio.wait_for(started.wait(), 2)
                self.assertIn('50%', str(screen.query_one('#upload-status', Static).content))
                app.action_upload()
                self.assertIs(app.screen, screen)
                app.selected = api.dialogs[1]
                await app._refresh_live_message(2)
                await pilot.pause()
                self.assertEqual(api.send_file.await_count, 1)
                finish.set()
                await pilot.pause()
                self.assertNotIsInstance(app.screen, UploadScreen)
                self.assertNotIn(screen, app.screen_stack)
                self.assertFalse(screen.is_attached)
                api.send_file.assert_awaited_once_with(api.dialogs[0], path, 'caption [literal]', progress_callback=unittest.mock.ANY)
                self.assertIs(app.selected, api.dialogs[1])

    async def test_upload_validation_failure_retry_cancel_and_no_input_loss(self):
        api = backend()
        api.send_file.side_effect = RuntimeError('offline')
        app = OfflineApp(api)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'file.txt'
            path.touch()
            async with app.run_test(size=(72, 25)) as pilot:
                app.action_upload()
                self.assertNotIsInstance(app.screen, UploadScreen)
                app.selected = api.dialogs[0]
                await pilot.press('s')
                screen = app.screen
                field = screen.query_one('#upload-path', Input)
                caption = screen.query_one('#upload-caption', Input)
                for value in (str(path), directory, str(path / 'missing')):
                    field.value = value
                    await pilot.pause(.2)
                    await pilot.click('#upload-send')
                    self.assertIn('Upload failed', str(screen.query_one('#upload-status', Static).content))
                api.send_file.assert_not_awaited()
                path.write_text('hello')
                field.value = str(path)
                caption.value = 'keep me'
                await pilot.pause(.2)
                await pilot.click('#upload-send')
                await pilot.pause()
                self.assertEqual(field.value, str(path))
                self.assertEqual(caption.value, 'keep me')
                self.assertFalse(field.disabled)
                self.assertIn('offline', str(screen.query_one('#upload-status', Static).content))
                api.send_file.side_effect = None
                await pilot.pause(.2)
                await pilot.click('#upload-send')
                await pilot.pause()
                self.assertEqual(api.send_file.await_count, 2)
                self.assertNotIsInstance(app.screen, UploadScreen)
                await pilot.press('s', 'escape')
                self.assertNotIsInstance(app.screen, UploadScreen)
                self.assertEqual(api.send_file.await_count, 2)

    async def test_cancel_running_upload_preserves_form_and_can_close(self):
        api = backend()
        started = asyncio.Event()

        async def send(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        api.send_file.side_effect = send
        app = OfflineApp(api)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'file.txt'
            path.write_text('hi')
            async with app.run_test() as pilot:
                app.selected = api.dialogs[0]
                await pilot.press('s')
                screen = app.screen
                screen.query_one('#upload-path', Input).value = str(path)
                screen.query_one('#upload-caption', Input).value = 'draft'
                await pilot.click('#upload-send')
                await asyncio.wait_for(started.wait(), 2)
                await pilot.press('escape')
                self.assertIs(app.screen, screen)
                self.assertIsNone(screen.worker)
                self.assertEqual(screen.query_one('#upload-caption', Input).value, 'draft')
                self.assertIn('cancelled', str(screen.query_one('#upload-status', Static).content))
                await pilot.press('escape')
                self.assertNotIn(screen, app.screen_stack)
                self.assertFalse(screen.is_attached)

    async def test_cancel_before_worker_start_and_unmount_during_upload(self):
        api = backend()
        app = OfflineApp(api)
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def send(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        api.send_file.side_effect = send
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'file.txt'
            path.write_text('hi')
            async with app.run_test() as pilot:
                app.selected = api.dialogs[0]
                await pilot.press('s')
                screen = app.screen
                screen.query_one('#upload-path', Input).value = str(path)
                await screen.on_button_pressed(Button.Pressed(screen.query_one('#upload-send', Button)))
                await screen.action_cancel()
                await pilot.pause()
                self.assertIsNone(screen.worker)
                self.assertFalse(screen.query_one('#upload-send', Button).disabled)
                api.send_file.assert_not_awaited()
                await screen.on_button_pressed(Button.Pressed(screen.query_one('#upload-send', Button)))
                await asyncio.wait_for(started.wait(), 2)
                worker = screen.worker
                screen.dismiss()
                await pilot.pause()
                self.assertTrue(worker.is_finished)
                self.assertTrue(cancelled.is_set())
                self.assertFalse(screen.is_attached)

    async def test_send_failure_retains_text_and_refresh_failure_is_not_send_failure(self):
        api = backend()
        app = OfflineApp(api)
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            composer = app.query_one('#composer', Input)
            composer.value = ' keep this '
            api.send_message.side_effect = RuntimeError('offline')
            await app.on_input_submitted(Input.Submitted(composer, composer.value))
            self.assertEqual(composer.value, ' keep this ')
            api.send_message.side_effect = None
            api.load_dialogs.side_effect = RuntimeError('refresh error')
            await app.on_input_submitted(Input.Submitted(composer, composer.value))
            self.assertEqual(composer.value, '')
            self.assertIn('Sent to Alice; refresh failed', str(app.query_one('#status', Static).content))

    async def test_send_does_not_clear_new_draft_or_retarget_after_switch(self):
        api = backend()
        app = OfflineApp(api)
        started, finish = asyncio.Event(), asyncio.Event()

        async def send(*args):
            started.set()
            await finish.wait()

        api.send_message.side_effect = send
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            composer = app.query_one('#composer', Input)
            composer.value = 'old'
            task = asyncio.create_task(app.on_input_submitted(Input.Submitted(composer, 'old')))
            await started.wait()
            app.selected = api.dialogs[1]
            composer.value = 'new'
            await app.on_input_submitted(Input.Submitted(composer, 'new'))
            finish.set()
            await task
            api.send_message.assert_awaited_once_with(api.dialogs[0], 'old')
            self.assertEqual(composer.value, 'new')
            api.messages.assert_not_awaited()

    async def test_media_picker_selects_older_item_and_keeps_original_dialog(self):
        api = backend()
        app = OfflineApp(api)
        old = Message('Alice', '', datetime.now(), False, media_kind='gif', media_name='old.gif', media_size=1024, source=object())
        new = Message('Alice', '', datetime.now(), False, media_kind='video', media_name='new.mp4', source=object())
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            app._current_messages = [old, new]
            app.query_one('#dialogs', ChatListView).focus()
            await pilot.press('v')
            screen = app.screen
            self.assertIsInstance(screen, MediaScreen)
            self.assertIn('1,024 bytes', media_label(old))
            app.selected = api.dialogs[1]
            await app._refresh_live_message(2)
            with patch('telegram_tui.app.resolve_trusted_binary', return_value='/usr/bin/mpv'), patch('telegram_tui.app.subprocess.Popen') as launch:
                screen.query_one(ListView).focus()
                await pilot.press('down', 'enter')
                await pilot.pause()
                api.cache_media_for_message.assert_awaited_once_with(1, old.source)
                self.assertIn('--loop-file=inf', launch.call_args.args[0])

    async def test_hydration_during_modal_and_small_viewport_image_ratio(self):
        api = backend()
        app = OfflineApp(api)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.png'
            Image.new('RGB', (960, 540)).save(path)
            photo = Message('Alice', '', datetime.now(), False, media_kind='photo', source=object())
            api.load_message_preview = AsyncMock(return_value=(path, None, None))
            with patch.dict('os.environ', {'OMAGRAM_IMAGE_MODE': 'halfcell'}):
                async with app.run_test(size=(40, 24)) as pilot:
                    app.selected = api.dialogs[0]
                    app.action_upload()
                    await app._hydrate_media(app.selected, [photo], app._chat_generation)
                    await pilot.pause()
                    self.assertIsInstance(app.screen, UploadScreen)
                    image = app.query_one(HalfcellImage)
                    width, height = image.content_size
                    self.assertLessEqual(width, app.query_one('#messages').content_size.width)
                    self.assertAlmostEqual(width * 10 / (height * 20), 960 / 540, delta=.35)
                    await pilot.press('escape')
                    app.query_one('#messages', MessagePanel).clear()
                    app.query_one('#messages', MessagePanel).write(terminal_image(Image.new('RGB', (40, 40))))
                    await pilot.pause()
                    self.assertLessEqual(app.query_one(HalfcellImage).content_size.height, 2)

    async def test_halfcell_and_tgp_have_visible_proportional_height(self):
        for mode, widget_type in [('halfcell', HalfcellImage), ('tgp', TGPImage)]:
            for width in (24, 64, 104):
                with self.subTest(mode=mode, width=width), patch.dict('os.environ', {'OMAGRAM_IMAGE_MODE': mode}), patch('textual_image.renderable.tgp._send_tgp_message'):
                    app = OfflineApp(backend())
                    async with app.run_test(size=(width, 34)) as pilot:
                        app.query_one('#sidebar').add_class('hidden')
                        panel = app.query_one(MessagePanel)
                        panel.clear()
                        panel.write(terminal_image(Image.new('RGB', (480, 270), 'blue')))
                        await pilot.pause()
                        widget = app.query_one(widget_type)
                        image_width, image_height = widget.content_size
                        self.assertGreater(image_height, 0)
                        self.assertLessEqual(image_height, 20)
                        self.assertLessEqual(image_width, panel.content_size.width)
                        self.assertLessEqual(image_width, 48)
                        self.assertAlmostEqual(image_width * 10 / (image_height * 20), 480 / 270, delta=.2)
                        if mode == 'halfcell':
                            self.assertIn('▀', ''.join(strip.text for strip in widget.render_lines(widget.region.reset_offset)))

    async def test_reload_preserves_id_and_visible_rows_ignore_backend_reordering(self):
        api = backend()
        app = OfflineApp(api)
        alice, bob = api.dialogs
        async with app.run_test() as pilot:
            await app._reload_dialogs()
            view = app.query_one(ChatListView)
            view.index = 1
            app.selected = bob
            api.load_dialogs.return_value = [bob, alice]
            await app._reload_dialogs()
            self.assertEqual(view.index, 0)
            self.assertEqual(view.highlighted_child.dialog.id, bob.id)
            api.dialogs = [alice, bob]
            await app.on_list_view_selected(ListView.Selected(view, view.children[0], 0))
            self.assertIs(app.selected, bob)
            await pilot.pause()
            app.selected = None
            app.action_compose()
            self.assertIs(app.selected, bob)
            await pilot.pause()

    async def test_concurrent_reloads_serialized_and_old_rows_valid_during_fetch(self):
        api = backend()
        app = OfflineApp(api)
        alice, bob = api.dialogs
        started, finish = asyncio.Event(), asyncio.Event()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            if calls == 1:
                api.dialogs = [bob, alice]
                started.set()
                await finish.wait()
            return [bob, alice]

        async with app.run_test() as pilot:
            await app._reload_dialogs()
            view = app.query_one(ChatListView)
            api.load_dialogs.side_effect = load
            first = asyncio.create_task(app._reload_dialogs())
            await started.wait()
            second = asyncio.create_task(app._reload_dialogs())
            await asyncio.sleep(0)
            self.assertEqual(calls, 1)
            await app.on_list_view_selected(ListView.Selected(view, view.children[0], 0))
            self.assertIs(app.selected, alice)
            finish.set()
            await asyncio.gather(first, second)
            self.assertEqual([item.dialog.id for item in view.children], [2, 1])
            self.assertEqual(view.highlighted_child.dialog.id, 1)

    async def test_stale_message_error_cannot_replace_new_chat(self):
        api = backend()
        app = OfflineApp(api)
        started, finish = asyncio.Event(), asyncio.Event()

        async def messages(dialog):
            if dialog.id == 1:
                started.set()
                await finish.wait()
                raise RuntimeError('stale error')
            return [Message('Bob', 'current chat', datetime.now(), False)]

        api.messages.side_effect = messages
        async with app.run_test() as pilot:
            app.selected = api.dialogs[0]
            first = asyncio.create_task(app._load_messages(app.selected))
            await started.wait()
            app.selected = api.dialogs[1]
            await app._load_messages(app.selected)
            finish.set()
            await first
            await pilot.pause()
            transcript = str([str(w.content) for w in app.query_one(MessagePanel).query(Static)])
            self.assertIn('current chat', transcript)
            self.assertNotIn('stale error', transcript)


if __name__ == '__main__':
    unittest.main()
