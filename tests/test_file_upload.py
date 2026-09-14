import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from PIL import Image
from telethon import TelegramClient
from telethon.tl import types

from telegram_tui.client import Dialog, MAX_MEDIA_BYTES, TelegramBackend


class FileUploadTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
