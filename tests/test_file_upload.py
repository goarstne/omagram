import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from telethon import TelegramClient
from telethon.tl import types

from telegram_tui.client import Dialog, MAX_MEDIA_BYTES, TelegramBackend


class FileUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_gifs_uses_telegram_saved_gifs(self):
        backend = object.__new__(TelegramBackend)
        gifs = [object(), object()]
        backend.client = AsyncMock(return_value=SimpleNamespace(gifs=gifs))
        result = await backend.recent_gifs()
        self.assertEqual(result, gifs)
        backend.client.assert_awaited_once()

    async def test_recent_gif_previews_never_download_full_media(self):
        backend = object.__new__(TelegramBackend)
        document = SimpleNamespace(id=42)
        backend.client = AsyncMock(return_value=SimpleNamespace(gifs=[document]))
        backend._cache_gif_thumbnail = AsyncMock(return_value=None)
        result = await backend.recent_gifs(limit=12, include_previews=True)
        self.assertEqual(len(result), 1)
        backend._cache_gif_thumbnail.assert_awaited_once_with(
            "saved-gifs", document, allow_media_fallback=False, timeout=3,
        )

    async def test_send_gif_uses_native_animated_media_path(self):
        backend = object.__new__(TelegramBackend)
        backend.client = SimpleNamespace(send_file=AsyncMock())
        dialog = Dialog(1, "Recipient", 0, object())
        gif = object()
        await backend.send_gif(dialog, gif)
        backend.client.send_file.assert_awaited_once_with(
            dialog.entity, gif, caption="", parse_mode=None,
            force_document=False, nosound_video=False,
        )

    async def test_telethon_keeps_jpeg_as_unmodified_document(self):
        client = TelegramClient(None, 1, "test")
        backend = object.__new__(TelegramBackend)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "photo.jpg"
            Image.new("RGB", (32, 32), "red").save(path)
            original = path.read_bytes()

            async def upload(stream, **kwargs):
                self.assertEqual(stream.read(), original)
                return types.InputFile(1, 1, path.name, "")

            async def convert(entity, stream, **kwargs):
                _, media, as_image = await client._file_to_media(
                    stream, force_document=kwargs["force_document"],
                    file_size=kwargs["file_size"], attributes=kwargs["attributes"],
                )
                self.assertFalse(as_image)
                self.assertIsInstance(media, types.InputMediaUploadedDocument)
                self.assertEqual(media.attributes[0].file_name, path.name)

            client.upload_file = AsyncMock(side_effect=upload)
            backend.client = SimpleNamespace(send_file=AsyncMock(side_effect=convert))
            await backend.send_file(Dialog(1, "Recipient", 0, object()), path)
            client.upload_file.assert_awaited_once()

    async def test_upload_preserves_bytes_name_caption_and_progress(self):
        backend = object.__new__(TelegramBackend)
        dialog = Dialog(1, "Recipient", 0, object())
        progress = lambda sent, total: None
        handles = []

        async def upload(entity, handle, **kwargs):
            self.assertIs(entity, dialog.entity)
            self.assertEqual(handle.read(), b"file contents")
            self.assertEqual(kwargs["file_size"], 13)
            self.assertEqual(kwargs["attributes"][0].file_name, "a file.txt")
            self.assertEqual(kwargs["caption"], "**literal**")
            self.assertIsNone(kwargs["parse_mode"])
            self.assertTrue(kwargs["force_document"])
            self.assertIs(kwargs["progress_callback"], progress)
            handles.append(handle)

        backend.client = SimpleNamespace(send_file=AsyncMock(side_effect=upload))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a file.txt"
            path.write_bytes(b"file contents")
            await backend.send_file(dialog, path, "**literal**", progress)
        self.assertTrue(handles[0].closed)

    async def test_gif_upload_marks_document_as_animated(self):
        backend = object.__new__(TelegramBackend)
        captured = {}

        async def send_file(entity, handle, **kwargs):
            captured.update(kwargs)

        backend.client = SimpleNamespace(send_file=AsyncMock(side_effect=send_file))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reaction.gif"
            path.write_bytes(b"GIF89a test")
            await backend.send_file(Dialog(1, "Recipient", 0, object()), path)
        self.assertTrue(any(isinstance(attr, types.DocumentAttributeAnimated) for attr in captured["attributes"]))
        self.assertEqual(captured["mime_type"], "image/gif")
        # force_document=True would make Telegram show the GIF as a static
        # file attachment instead of an animated/looping document.
        self.assertFalse(captured["force_document"])
        self.assertTrue(
            any(isinstance(attr, types.DocumentAttributeFilename) and attr.file_name == "reaction.gif"
                for attr in captured["attributes"])
        )

    async def test_invalid_files_never_reach_telegram(self):
        backend = object.__new__(TelegramBackend)
        backend.client = SimpleNamespace(send_file=AsyncMock())
        dialog = Dialog(1, "Recipient", 0, object())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            empty = root / "empty"
            empty.touch()
            huge = root / "huge"
            with huge.open("wb") as stream:
                stream.truncate(MAX_MEDIA_BYTES + 1)
            fifo = root / "pipe"
            os.mkfifo(fifo)
            for path in (root, root / "missing", empty, huge, fifo):
                with self.subTest(path=path.name), self.assertRaises((OSError, ValueError)):
                    await backend.send_file(dialog, path)
            with self.assertRaises(ValueError):
                await backend.send_file(dialog, empty, "😀" * 513)
        backend.client.send_file.assert_not_awaited()

    async def test_failure_propagates_and_closes_file(self):
        backend = object.__new__(TelegramBackend)
        handles = []

        async def fail(entity, handle, **kwargs):
            handles.append(handle)
            raise OSError("connection lost")

        backend.client = SimpleNamespace(send_file=AsyncMock(side_effect=fail))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "file.txt"
            path.write_text("data")
            with self.assertRaisesRegex(OSError, "connection lost"):
                await backend.send_file(Dialog(1, "Recipient", 0, object()), path)
        self.assertTrue(handles[0].closed)

    def test_folder_filter_evaluates_category_and_exclude_flags(self):
        backend = object.__new__(TelegramBackend)
        alice = Dialog(1, "Alice", 2, types.PeerUser(1), is_private=True, is_contact=True)
        stranger = Dialog(2, "Stranger", 0, types.PeerUser(2), is_private=True)
        bot = Dialog(3, "Helper Bot", 0, types.PeerUser(3), is_private=True, is_bot=True)
        team = Dialog(4, "Team", 1, types.PeerChat(4), is_group=True)
        old_team = Dialog(5, "Old Team", 0, types.PeerChat(5), is_group=True, is_archived=True)
        news = Dialog(6, "News", 5, types.PeerChannel(6), is_channel=True, is_muted=True)
        dialogs = [alice, stranger, bot, team, old_team, news]

        def make_filter(fid, **flags):
            return types.DialogFilter(fid, types.TextWithEntities("F", []), [], [], [], **flags)

        backend._custom_filters = {"folder:1": make_filter(1, bots=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:1")], [3])

        backend._custom_filters = {"folder:2": make_filter(2, contacts=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:2")], [1])

        backend._custom_filters = {"folder:3": make_filter(3, non_contacts=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:3")], [2])

        backend._custom_filters = {"folder:4": make_filter(4, groups=True, exclude_archived=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:4")], [4])

        backend._custom_filters = {"folder:5": make_filter(5, broadcasts=True, exclude_muted=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:5")], [])

        # bot has unread=0, so exclude_read must drop it even though bots=True matches its category.
        backend._custom_filters = {"folder:6": make_filter(6, bots=True, exclude_read=True)}
        self.assertEqual([d.id for d in backend._filter_dialogs(dialogs, "folder:6")], [])

    def test_dialog_is_muted_reflects_notify_settings_mute_until(self):
        now = datetime.now(timezone.utc)
        future = SimpleNamespace(dialog=SimpleNamespace(
            notify_settings=SimpleNamespace(mute_until=now + timedelta(days=1))
        ))
        past = SimpleNamespace(dialog=SimpleNamespace(
            notify_settings=SimpleNamespace(mute_until=now - timedelta(days=1))
        ))
        unset = SimpleNamespace(dialog=SimpleNamespace(notify_settings=SimpleNamespace(mute_until=None)))
        self.assertTrue(TelegramBackend._dialog_is_muted(future))
        self.assertFalse(TelegramBackend._dialog_is_muted(past))
        self.assertFalse(TelegramBackend._dialog_is_muted(unset))

    def test_static_gif_thumb_skips_video_size_preview(self):
        video_thumb = types.VideoSize("u", 100, 100, 5000, 0)
        small_photo = types.PhotoSize("s", 90, 90, 500)
        large_photo = types.PhotoSize("m", 180, 180, 4000)
        document = SimpleNamespace(thumbs=[small_photo, video_thumb, large_photo])
        # thumb=-1 would pick video_thumb (VideoSize sorts highest in Telethon).
        self.assertEqual(TelegramBackend._static_gif_thumb(document), large_photo.type)
        # A Message-like wrapper exposing .document should work the same way.
        message = SimpleNamespace(document=document)
        self.assertEqual(TelegramBackend._static_gif_thumb(message), large_photo.type)
        # No static sizes at all -> no crash, caller falls back to thumb=-1.
        self.assertIsNone(TelegramBackend._static_gif_thumb(SimpleNamespace(thumbs=[video_thumb])))

    async def test_picker_skips_video_thumb_when_no_static_preview_exists(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            with patch.dict("os.environ", {"XDG_CACHE_HOME": cache_dir}):
                backend = TelegramBackend(1, "hash", str(Path(session_dir) / "session"))
            backend.client.download_media = AsyncMock()
            item = SimpleNamespace(
                id=42,
                document=SimpleNamespace(thumbs=[types.VideoSize("u", 100, 100, 5000, 0)]),
            )
            try:
                result = await backend._cache_gif_thumbnail(
                    "saved-gifs", item, allow_media_fallback=False,
                )
                self.assertIsNone(result)
                backend.client.download_media.assert_not_awaited()
            finally:
                backend.client.session.close()

    async def test_recent_gif_previews_are_concurrency_capped(self):
        backend = object.__new__(TelegramBackend)
        documents = [SimpleNamespace(id=i) for i in range(10)]
        backend.client = AsyncMock(return_value=SimpleNamespace(gifs=documents))

        in_flight = 0
        max_in_flight = 0
        release = asyncio.Event()

        async def fake_cache_gif_thumbnail(*_args, **_kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.wait_for(release.wait(), timeout=5)
            in_flight -= 1
            return None

        backend._cache_gif_thumbnail = fake_cache_gif_thumbnail

        async def run_and_release():
            task = asyncio.ensure_future(backend.recent_gifs(include_previews=True))
            await asyncio.sleep(0.05)
            self.assertLessEqual(max_in_flight, 4)
            release.set()
            return await task

        results = await run_and_release()
        self.assertEqual(len(results), len(documents))
        self.assertEqual(max_in_flight, 4)


if __name__ == "__main__":
    unittest.main()
