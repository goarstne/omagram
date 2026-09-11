from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Awaitable, Callable
from urllib.request import Request, urlopen
import uuid

from PIL import Image, ImageStat
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl import types


logger = logging.getLogger("omagram.telegram")


@dataclass(slots=True)
class Dialog:
    id: int
    title: str
    unread: int
    entity: object
    preview: str = ""
    preview_date: datetime | None = None


@dataclass(slots=True)
class Message:
    sender: str
    text: str
    timestamp: datetime
    outgoing: bool
    media_kind: str | None = None
    media_path: Path | None = None
    media_name: str | None = None
    media_size: int | None = None
    youtube_url: str | None = None
    youtube_thumbnail_path: Path | None = None
    source: object | None = None


YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)[\w-]+|youtu\.be/[\w-]+)(?:\S*)?",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,})", re.IGNORECASE)


class TelegramBackend:
    """Small adapter keeping Telethon details out of the TUI."""

    def __init__(self, api_id: int, api_hash: str, session: str):
        # Allow enough connection retries and reasonable timeouts so that
        # cross-datacenter migrations (e.g. DC 4) and media transfers succeed
        # reliably without hanging the initial connect.
        self.client = TelegramClient(
            session,
            api_id,
            api_hash,
            device_model="PC 64bit",
            system_version="Linux",
            app_version="5.0.0",
            lang_code="de",
            system_lang_code="de",
            timeout=10,
            request_retries=3,
            connection_retries=2,
            retry_delay=0.5,
        )
        self.media_cache = Path.home() / ".cache/omagram/media"
        self.dialogs: list[Dialog] = []
        self.on_new_message: Callable[[int], Awaitable[None]] | None = None
        logger.info(
            "backend initialized session=%s connected=%s timeout=%s connection_retries=%s request_retries=%s",
            session,
            self.client.is_connected(),
            10,
            2,
            3,
        )

        @self.client.on(events.NewMessage)
        async def _new_message(event: events.NewMessage.Event) -> None:
            logger.info("new message event chat_id=%s", event.chat_id)
            if self.on_new_message:
                await self.on_new_message(event.chat_id or 0)

    async def connect(self) -> None:
        started = time.perf_counter()
        logger.info("connect started connected_before=%s", self.client.is_connected())
        try:
            result = await self.client.connect()
            logger.info(
                "connect completed connected=%s elapsed=%.3fs result=%r",
                self.client.is_connected(),
                time.perf_counter() - started,
                result,
            )
        except Exception:
            logger.exception("connect failed elapsed=%.3fs", time.perf_counter() - started)
            raise

    async def authorized(self) -> bool:
        started = time.perf_counter()
        logger.info("authorization check started connected=%s", self.client.is_connected())
        try:
            result = await self.client.is_user_authorized()
            logger.info("authorization check completed authorized=%s elapsed=%.3fs", result, time.perf_counter() - started)
            return result
        except Exception:
            logger.exception("authorization check failed elapsed=%.3fs", time.perf_counter() - started)
            raise

    async def disconnect(self) -> None:
        """Close a partially connected client so retries start cleanly."""
        if not self.client.is_connected():
            return
        started = time.perf_counter()
        logger.info("disconnect started")
        try:
            await self.client.disconnect()
            logger.info("disconnect completed elapsed=%.3fs", time.perf_counter() - started)
        except Exception:
            logger.exception("disconnect failed elapsed=%.3fs", time.perf_counter() - started)
            raise

    async def send_code(self, phone: str) -> None:
        started = time.perf_counter()
        logger.info("phone-code request started phone_present=%s", bool(phone))
        try:
            await self.client.send_code_request(phone)
            logger.info("phone-code request completed elapsed=%.3fs", time.perf_counter() - started)
        except Exception:
            logger.exception("phone-code request failed elapsed=%.3fs", time.perf_counter() - started)
            raise

    async def sign_in(self, phone: str, code: str, password: str | None = None) -> None:
        started = time.perf_counter()
        logger.info("phone-code sign-in started phone_present=%s code_present=%s", bool(phone), bool(code))
        try:
            await self.client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError as exc:
            logger.info("phone-code sign-in requires 2fa")
            if not password:
                raise RuntimeError("Dieses Konto benötigt zusätzlich das 2FA-Passwort.") from exc
            await self.client.sign_in(password=password)
        except Exception:
            logger.exception("phone-code sign-in failed elapsed=%.3fs", time.perf_counter() - started)
            raise
        logger.info("phone-code sign-in completed elapsed=%.3fs", time.perf_counter() - started)

    async def load_dialogs(self, limit: int = 60) -> list[Dialog]:
        started = time.perf_counter()
        logger.info("dialog load started limit=%s connected=%s", limit, self.client.is_connected())
        try:
            self.dialogs = [
                Dialog(
                    id=dialog.id,
                    title=dialog.name or "(ohne Titel)",
                    unread=dialog.unread_count,
                    entity=dialog.entity,
                    preview=(dialog.message.message if dialog.message else "") or ("[media]" if dialog.message and dialog.message.media else ""),
                    preview_date=dialog.message.date if dialog.message else None,
                )
                async for dialog in self.client.iter_dialogs(limit=limit)
                if dialog.is_user or dialog.is_group or dialog.is_channel
            ]
            logger.info("dialog load completed count=%s elapsed=%.3fs", len(self.dialogs), time.perf_counter() - started)
            return self.dialogs
        except Exception:
            logger.exception("dialog load failed limit=%s elapsed=%.3fs", limit, time.perf_counter() - started)
            raise

    async def messages(self, dialog: Dialog, limit: int = 80) -> list[Message]:
        started = time.perf_counter()
        logger.info("message load started dialog_id=%s limit=%s", dialog.id, limit)
        result: list[Message] = []
        # Telethon returns newest messages first. Reverse the bounded result so
        # the reader renders the selected window in chronological order.
        try:
            items = await self.client.get_messages(dialog.entity, limit=limit)
        except Exception:
            logger.exception("message load request failed dialog_id=%s elapsed=%.3fs", dialog.id, time.perf_counter() - started)
            raise
        ordered_items = list(reversed(items))
        for item in ordered_items:
            text = item.message or ""
            media_kind = self._media_kind(item)
            if not text:
                media = getattr(item, "media", None)
                if isinstance(media, types.MessageMediaPoll):
                    question = getattr(getattr(media, "poll", None), "question", "")
                    text = f"[Poll: {question}]" if question else "[Poll]"
                elif isinstance(media, types.MessageMediaContact):
                    name = f"{getattr(media, 'first_name', '')} {getattr(media, 'last_name', '')}".strip()
                    text = f"[Contact: {name}]" if name else "[Contact]"
                elif isinstance(media, types.MessageMediaGeo):
                    text = "[Location]"
                elif media_kind:
                    text = f"[{media_kind}]"
            if not text and not media_kind:
                continue
            sender = "you" if item.out else (getattr(item.sender, "first_name", None) or dialog.title)
            media_path = None
            media_name = None
            media_size = None
            if item.media:
                media_name = getattr(getattr(item, "file", None), "name", None)
                media_size = getattr(getattr(item, "file", None), "size", None)
            youtube_match = YOUTUBE_RE.search(text)
            youtube_url = youtube_match.group(0) if youtube_match else None
            result.append(
                Message(
                    sender,
                    text,
                    item.date,
                    item.out,
                    media_kind=media_kind,
                    media_path=media_path,
                    media_name=media_name,
                    media_size=media_size,
                    youtube_url=youtube_url,
                    youtube_thumbnail_path=None,
                    source=item,
                )
            )
        logger.info(
            "message load completed dialog_id=%s fetched=%s returned=%s media=%s elapsed=%.3fs",
            dialog.id,
            len(items),
            len(result),
            sum(message.media_kind is not None for message in result),
            time.perf_counter() - started,
        )
        return result

    async def load_message_preview(
        self, dialog_id: int, message: Message, enabled: bool = True
    ) -> tuple[Path | None, str | None, Path | None]:
        """Load one small preview on demand, outside the initial chat render."""
        if not enabled or message.source is None:
            return message.media_path, message.youtube_url, message.youtube_thumbnail_path
        return await self._load_message_preview(dialog_id, message.source, enabled)

    async def _load_message_preview(
        self, dialog_id: int, item: object, enabled: bool
    ) -> tuple[Path | None, str | None, Path | None]:
        """Load small previews concurrently without downloading full videos."""
        if not enabled:
            return None, None, None
        media_kind = self._media_kind(item)
        media_path = (
            await self._cache_media_thumbnail(dialog_id, item)
            if media_kind in {"photo", "gif", "video"}
            else None
        )
        text = getattr(item, "message", "") or ""
        youtube_match = YOUTUBE_RE.search(text)
        youtube_url = youtube_match.group(0) if youtube_match else None
        youtube_thumbnail_path = (
            await self._cache_youtube_thumbnail(youtube_url)
            if youtube_url
            else None
        )
        return media_path, youtube_url, youtube_thumbnail_path

    @staticmethod
    def _media_kind(item: object) -> str | None:
        media = getattr(item, "media", None)
        if isinstance(media, types.MessageMediaWebPage):
            return None
        if getattr(item, "photo", None):
            return "photo"
        if getattr(item, "gif", None):
            return "gif"
        doc = getattr(item, "document", None)
        if doc:
            mime = getattr(doc, "mime_type", "") or ""
            name = getattr(getattr(item, "file", None), "name", "") or ""
            if mime == "image/gif" or name.lower().endswith(".gif"):
                return "gif"
        if getattr(item, "video", None):
            return "video"
        if doc:
            return "file"
        return None

    @staticmethod
    def _valid_image(path: Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
            return True
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError):
            return False

    @classmethod
    def _usable_gif_image(cls, path: Path) -> bool:
        """Reject corrupt, degenerate, and effectively-black Telegram GIF frames.

        The size floor only guards against stub/placeholder images (e.g. a
        1x1 icon); Telegram's embedded document thumbnail is frequently
        smaller than a typical video poster frame and is otherwise a
        perfectly usable preview once scaled for the terminal.
        """
        if not cls._valid_image(path):
            return False
        try:
            with Image.open(path) as image:
                if image.width < 24 or image.height < 24:
                    logger.debug(
                        "gif frame rejected too small path=%s size=%sx%s",
                        path, image.width, image.height,
                    )
                    return False
                rgb = image.convert("RGB")
                stat = ImageStat.Stat(rgb)
                mean = sum(stat.mean) / 3
                maximum = max(channel_max for _, channel_max in rgb.getextrema())
                # Telegram sometimes exposes a black poster frame while the
                # actual animated document contains useful later frames.
                if mean < 8 and maximum < 64:
                    logger.debug(
                        "gif frame rejected too dark path=%s mean=%.1f max=%s",
                        path, mean, maximum,
                    )
                    return False
                return True
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError):
            return False

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove incomplete media path=%s", path, exc_info=True)

    async def _cache_media(self, dialog_id: int, item: object) -> Path | None:
        started = time.perf_counter()
        target = self.media_cache / str(dialog_id)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        extension = getattr(getattr(item, "file", None), "ext", "") or ""
        if not extension:
            media_kind = self._media_kind(item)
            if media_kind in {"gif", "video"}:
                extension = ".mp4"
            elif media_kind == "photo":
                extension = ".jpg"
        if extension and not extension.startswith("."):
            extension = f".{extension}"

        requested_path = target / f"{message_id}{extension}"
        temp_path = target / f".{message_id}.tmp-{uuid.uuid4().hex}{extension}"
        existing = requested_path if requested_path.is_file() else None
        if existing and existing.is_file() and existing.stat().st_size > 0:
            logger.debug("media cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, existing)
            return existing

        cached_alternatives = [
            p for p in target.glob(f"{message_id}.*")
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp") and p.stat().st_size > 0
        ]
        if cached_alternatives:
            logger.debug("media cache hit alternative dialog_id=%s message_id=%s path=%s", dialog_id, message_id, cached_alternatives[0])
            return cached_alternatives[0]

        if existing:
            logger.warning("invalid media cache removed dialog_id=%s message_id=%s path=%s", dialog_id, message_id, existing)
            self._remove_file(existing)
        self._remove_file(temp_path)
        try:
            logger.info("media download started dialog_id=%s message_id=%s kind=%s", dialog_id, message_id, self._media_kind(item))
            downloaded = await asyncio.wait_for(
                self.client.download_media(item, file=str(temp_path)), timeout=90
            )
            path = Path(downloaded) if downloaded else None
            valid = path if path and path.is_file() and path.stat().st_size > 0 else None
            if valid:
                path.replace(requested_path)
                valid = requested_path
            logger.info(
                "media download completed dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                dialog_id,
                message_id,
                valid is not None,
                valid.stat().st_size if valid else 0,
                time.perf_counter() - started,
            )
            return valid
        except asyncio.CancelledError:
            logger.warning("media download cancelled dialog_id=%s message_id=%s", dialog_id, message_id)
            raise
        except Exception:
            # A failed preview must never make the whole chat unreadable.
            logger.exception("media download failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
            return None
        finally:
            self._remove_file(temp_path)

    async def cache_media_for_message(self, dialog_id: int, item: object) -> Path | None:
        """Download a media message on demand, e.g. before opening it in mpv."""
        return await self._cache_media(dialog_id, item)

    async def _cache_media_thumbnail(self, dialog_id: int, item: object) -> Path | None:
        if self._media_kind(item) == "gif":
            return await self._cache_gif_thumbnail(dialog_id, item)
        started = time.perf_counter()
        target = self.media_cache / str(dialog_id) / "thumbs"
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        requested_path = target / f"{message_id}.jpg"
        temp_path = target / f".{message_id}.tmp-{uuid.uuid4().hex}.jpg"
        existing = requested_path if requested_path.is_file() else None
        is_video = self._media_kind(item) == "video"
        valid_existing = (
            self._usable_gif_image(existing)
            if is_video and existing
            else self._valid_image(existing)
            if existing
            else False
        )
        if existing and existing.is_file() and existing.stat().st_size > 0 and valid_existing:
            logger.debug("thumbnail cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, existing)
            return existing
        if existing:
            logger.warning("invalid thumbnail cache removed dialog_id=%s message_id=%s path=%s", dialog_id, message_id, existing)
            self._remove_file(existing)
        self._remove_file(temp_path)
        try:
            logger.info("thumbnail download started dialog_id=%s message_id=%s kind=%s", dialog_id, message_id, self._media_kind(item))
            downloaded = await asyncio.wait_for(
                self.client.download_media(item, file=str(temp_path), thumb=-1), timeout=25
            )
            path = Path(downloaded) if downloaded else None
            valid = (
                path
                if path
                and path.is_file()
                and path.stat().st_size > 0
                and (self._usable_gif_image(path) if is_video else self._valid_image(path))
                else None
            )
            if valid:
                path.replace(requested_path)
                valid = requested_path
            logger.info(
                "thumbnail download completed dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                dialog_id,
                message_id,
                valid is not None,
                valid.stat().st_size if valid else 0,
                time.perf_counter() - started,
            )
            return valid
        except asyncio.CancelledError:
            logger.warning("thumbnail download cancelled dialog_id=%s message_id=%s", dialog_id, message_id)
            raise
        except Exception:
            logger.exception("thumbnail download failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
            return None
        finally:
            self._remove_file(temp_path)
            self._remove_file(temp_path.with_suffix(".mp4"))

    async def _cache_gif_thumbnail(self, dialog_id: int, item: object) -> Path | None:
        """Return a validated GIF preview without exposing partial downloads."""
        started = time.perf_counter()
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        target = self.media_cache / str(dialog_id) / "thumbs"
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = target / f"{message_id}.jpg"
        temp_output = target / f".{message_id}.tmp-{uuid.uuid4().hex}.jpg"
        fallback = target / f".{message_id}.fallback-{uuid.uuid4().hex}.jpg"
        try:
            if self._usable_gif_image(output):
                logger.debug("gif thumbnail cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, output)
                return output
            self._remove_file(output)
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError):
            self._remove_file(output)

        # Never let an old interrupted transfer become the next render source.
        self._remove_file(temp_output)
        self._remove_file(fallback)

        # Telegram's embedded GIF thumbnail is cheap and prevents a selected
        # chat from showing only [gif] while the full MP4 is being fetched.
        try:
            logger.info("gif embedded thumbnail download started dialog_id=%s message_id=%s", dialog_id, message_id)
            downloaded = await asyncio.wait_for(
                self.client.download_media(item, file=str(fallback), thumb=-1), timeout=25
            )
            candidate = Path(downloaded) if downloaded else None
            if candidate and candidate.is_file() and self._usable_gif_image(candidate):
                candidate.replace(output)
                logger.info(
                    "gif embedded thumbnail used dialog_id=%s message_id=%s bytes=%s elapsed=%.3fs",
                    dialog_id, message_id, output.stat().st_size, time.perf_counter() - started,
                )
                return output
            elif candidate and candidate.is_file():
                logger.info(
                    "gif embedded thumbnail rejected (unusable/black) dialog_id=%s message_id=%s bytes=%s",
                    dialog_id, message_id, candidate.stat().st_size,
                )
                self._remove_file(candidate)
        except asyncio.CancelledError:
            self._remove_file(fallback)
            raise
        except (asyncio.TimeoutError, OSError) as exc:
            logger.warning(
                "gif embedded thumbnail timed out dialog_id=%s message_id=%s error=%s elapsed=%.3fs",
                dialog_id, message_id, str(exc) or type(exc).__name__, time.perf_counter() - started,
            )
            self._remove_file(fallback)
        except Exception:
            self._remove_file(fallback)
            logger.exception("gif embedded thumbnail failed dialog_id=%s message_id=%s", dialog_id, message_id)
        finally:
            self._remove_file(fallback)

        # If embedded thumbnail was missing or black, check for a cached full video
        # or download it if ffmpeg is available to extract a usable frame.
        media_dir = self.media_cache / str(dialog_id)
        cached_sources = sorted(
            path
            for path in media_dir.glob(f"{message_id}.*")
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith(".tmp") and path.stat().st_size > 0
        )
        source = cached_sources[0] if cached_sources else None
        ffmpeg = shutil.which("ffmpeg")

        if not source and ffmpeg:
            logger.info(
                "gif embedded thumb unavailable or unusable, fetching media source dialog_id=%s message_id=%s",
                dialog_id, message_id,
            )
            source = await self._cache_media(dialog_id, item)

        if not source or not ffmpeg:
            logger.warning(
                "gif preview unavailable dialog_id=%s message_id=%s cached_source=%s ffmpeg=%s elapsed=%.3fs",
                dialog_id, message_id, bool(source), bool(ffmpeg), time.perf_counter() - started,
            )
            return None

        def extract() -> Path | None:
            frame_prefix = temp_output.with_suffix("")
            frame_pattern = Path(f"{frame_prefix}-%03d.jpg")
            try:
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel", "error",
                        "-y",
                        "-i", str(source),
                        "-vf", "fps=2,scale=trunc(min(960\\,iw)/2)*2:-2",
                        "-frames:v", "12",
                        str(frame_pattern),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=15,
                )
                frames = sorted(target.glob(f"{frame_prefix.name}-*.jpg"))
                for frame in frames:
                    if self._usable_gif_image(frame):
                        frame.replace(output)
                        for leftover in frames:
                            self._remove_file(leftover)
                        return output
                for frame in frames:
                    self._remove_file(frame)
                return None
            except (OSError, subprocess.SubprocessError):
                for frame in target.glob(f"{frame_prefix.name}-*.jpg"):
                    self._remove_file(frame)
                return None

        result = await asyncio.to_thread(extract)
        if result is None:
            logger.warning("gif frame extraction failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
        else:
            logger.info(
                "gif thumbnail extracted dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                dialog_id, message_id, bool(result), result.stat().st_size if result else 0,
                time.perf_counter() - started,
            )
        if result and (not result.is_file() or result.stat().st_size == 0 or not self._usable_gif_image(result)):
            self._remove_file(result)
            result = None
        self._remove_file(temp_output)
        self._remove_file(fallback)
        return result

    async def _cache_youtube_thumbnail(self, url: str) -> Path | None:
        started = time.perf_counter()
        match = YOUTUBE_ID_RE.search(url)
        if not match:
            return None
        video_id = match.group(1).split("&", 1)[0]
        target = self.media_cache / "youtube"
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = target / f"{hashlib.sha256(video_id.encode()).hexdigest()[:20]}.jpg"
        if path.is_file() and self._valid_image(path):
            logger.debug("youtube thumbnail cache hit video_id=%s path=%s", video_id, path)
            return path
        temp_path = target / f".{path.name}.tmp-{uuid.uuid4().hex}.jpg"

        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        def download() -> Path | None:
            try:
                request = Request(thumbnail_url, headers={"User-Agent": "omagram/0.1"})
                with urlopen(request, timeout=5) as response:
                    data = response.read()
                temp_path.write_bytes(data)
                if self._valid_image(temp_path):
                    temp_path.replace(path)
                    return path
                self._remove_file(temp_path)
                return None
            except Exception:
                self._remove_file(temp_path)
                return None

        result = await asyncio.to_thread(download)
        logger.info(
            "youtube thumbnail completed video_id=%s valid=%s elapsed=%.3fs",
            video_id,
            result is not None,
            time.perf_counter() - started,
        )
        return result

    async def send_message(self, dialog: Dialog, text: str) -> None:
        await self.client.send_message(dialog.entity, text)
