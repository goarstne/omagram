from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram_tui.client import (
    DOWNLOAD_CHUNK_BYTES,
    MAX_MEDIA_BYTES,
    MAX_YOUTUBE_THUMBNAIL_BYTES,
    TelegramBackend,
)
from telegram_tui.security import MediaTooLargeError


def make_backend(cache_dir: str, session_dir: str) -> TelegramBackend:
    with patch.dict("os.environ", {"XDG_CACHE_HOME": cache_dir}):
        return TelegramBackend(1, "hash", str(Path(session_dir) / "session"))


def close_backend(backend: TelegramBackend) -> None:
    backend.client.session.close()


class SizeGuardTests(unittest.TestCase):
    def test_raises_once_cap_exceeded(self):
        guard = TelegramBackend._size_guard(100)
        guard(50, 1000)  # under cap: no error
        with self.assertRaises(MediaTooLargeError):
            guard(101, 1000)

    def test_declared_size_reads_file_size_attr(self):
        item = SimpleNamespace(file=SimpleNamespace(size=12345))
        self.assertEqual(TelegramBackend._declared_size(item), 12345)
        self.assertIsNone(TelegramBackend._declared_size(SimpleNamespace()))


class CacheMediaLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_declared_oversize_skips_download_entirely(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)
            backend.client.download_media = AsyncMock()
            item = SimpleNamespace(
                id=42,
                file=SimpleNamespace(size=MAX_MEDIA_BYTES + 1, ext=".mp4"),
                photo=None, gif=None, video=None, document=None, media=None,
            )
            result = await backend._cache_media(1, item)
            close_backend(backend)
        self.assertIsNone(result)
        backend.client.download_media.assert_not_awaited()

    async def test_progress_callback_abort_is_handled_cleanly(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)

            async def fake_download(item, file, progress_callback=None):
                progress_callback(MAX_MEDIA_BYTES + 1, None)
                return file

            backend.client.download_media = AsyncMock(side_effect=fake_download)
            item = SimpleNamespace(
                id=7,
                file=SimpleNamespace(size=None, ext=".mp4"),
                photo=None, gif=None, video=None, document=None, media=None,
            )
            result = await backend._cache_media(1, item)
            close_backend(backend)
        self.assertIsNone(result)

    async def test_unsafe_extension_falls_back_to_kind_default(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)
            written_paths = []

            async def fake_download(item, file, progress_callback=None):
                Path(file).write_bytes(b"data")
                written_paths.append(file)
                return file

            backend.client.download_media = AsyncMock(side_effect=fake_download)
            item = SimpleNamespace(
                id=99,
                file=SimpleNamespace(size=4, ext="/../../evil"),
                photo=True, gif=None, video=None, document=None, media=None,
            )
            result = await backend._cache_media(1, item)
            close_backend(backend)
        self.assertIsNotNone(result)
        self.assertEqual(result.suffix, ".jpg")  # photo fallback, not the unsafe raw ext
        self.assertTrue(written_paths[0].endswith(".jpg"))


class YoutubeThumbnailLimitsTests(unittest.IsolatedAsyncioTestCase):
    def _context_manager_response(self, headers: dict, chunks: list[bytes]):
        response = MagicMock()
        response.headers = MagicMock()
        response.headers.get.side_effect = lambda key, default=None: headers.get(key, default)
        response.read.side_effect = chunks
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    async def test_rejects_declared_content_length_over_cap(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)
            response = self._context_manager_response(
                {"Content-Length": str(MAX_YOUTUBE_THUMBNAIL_BYTES + 1)}, [b""]
            )
            with patch("telegram_tui.client.urlopen", return_value=response):
                result = await backend._cache_youtube_thumbnail("https://youtu.be/AbCdEfGhIjK")
            cache_files = list((Path(cache_dir) / "omagram/media/youtube").glob("*"))
            close_backend(backend)
        self.assertIsNone(result)
        response.read.assert_not_called()
        self.assertEqual(cache_files, [])

    async def test_aborts_mid_stream_without_content_length_header(self):
        oversize_chunk = b"x" * (DOWNLOAD_CHUNK_BYTES)
        chunk_count = MAX_YOUTUBE_THUMBNAIL_BYTES // DOWNLOAD_CHUNK_BYTES + 2
        chunks = [oversize_chunk] * chunk_count + [b""]
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)
            response = self._context_manager_response({}, chunks)
            with patch("telegram_tui.client.urlopen", return_value=response):
                result = await backend._cache_youtube_thumbnail("https://youtu.be/AbCdEfGhIjK")
            cache_dir_path = Path(cache_dir) / "omagram/media/youtube"
            leftover = [p for p in cache_dir_path.glob("*") if p.is_file()]
            close_backend(backend)
        self.assertIsNone(result)
        self.assertEqual(leftover, [])

    async def test_success_path_streams_and_caches_small_image(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as session_dir:
            backend = make_backend(cache_dir, session_dir)
            response = self._context_manager_response({}, [b"fake-jpeg-bytes", b""])
            with patch("telegram_tui.client.urlopen", return_value=response), patch.object(
                TelegramBackend, "_valid_image", return_value=True
            ):
                result = await backend._cache_youtube_thumbnail("https://youtu.be/AbCdEfGhIjK")
            self.assertIsNotNone(result)
            self.assertTrue(result.is_file())
            self.assertEqual(result.read_bytes(), b"fake-jpeg-bytes")
            close_backend(backend)


if __name__ == "__main__":
    unittest.main()
