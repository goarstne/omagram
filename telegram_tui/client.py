from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Awaitable, Callable
from urllib.request import Request, urlopen
import uuid

from PIL import Image, ImageStat
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl import functions, types
from telethon import utils

from .security import (
    MediaTooLargeError,
    VerifiedDir,
    open_verified_dir,
    resolve_trusted_binary,
    safe_extension,
    safe_subprocess_env,
)


logger = logging.getLogger("omagram.telegram")

# Hard caps so a single malicious/oversized message can't fill the disk or
# stall the UI. Declared Telegram file sizes are checked up front, and a
# progress callback aborts mid-transfer in case that size lied.
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024  # full video/gif on-demand download
MAX_THUMBNAIL_BYTES = 25 * 1024 * 1024  # embedded thumbnails / poster frames
MAX_YOUTUBE_THUMBNAIL_BYTES = 5 * 1024 * 1024  # hqdefault.jpg is normally <200KB
DOWNLOAD_CHUNK_BYTES = 64 * 1024


@dataclass(slots=True)
class Dialog:
    id: int
    title: str
    unread: int
    entity: object
    preview: str = ""
    preview_date: datetime | None = None
    is_private: bool = False
    is_group: bool = False
    is_channel: bool = False


@dataclass(slots=True, frozen=True)
class DialogTab:
    key: str
    title: str
    kind: str
    filter: object | None = None


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
            lang_code="en",
            system_lang_code="en",
            timeout=10,
            request_retries=3,
            connection_retries=2,
            retry_delay=0.5,
        )
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        # A dir_fd-anchored handle, not a Path: every cache operation below
        # is performed relative to this fd (or one opened from it the same
        # way), so a directory swapped out from under an already-verified
        # path can't redirect cache reads/writes -- the kernel keeps
        # resolving to the exact inode checked here. Held open for the
        # process's lifetime; a single long-lived fd for an interactive TUI
        # app is not a resource concern.
        with open_verified_dir(cache_home / "omagram", mode=0o700) as app_cache:
            self.media_cache: VerifiedDir = app_cache.subdir("media", mode=0o700)
        self.dialogs: list[Dialog] = []
        self.dialog_tabs: list[DialogTab] = [
            DialogTab("all", "Chats", "all"),
            DialogTab("private", "Private", "private"),
            DialogTab("groups", "Groups", "groups"),
        ]
        self._custom_filters: dict[str, object] = {}
        self._media_download_lock = asyncio.Lock()
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
                raise RuntimeError("This account additionally requires a 2FA password.") from exc
            await self.client.sign_in(password=password)
        except Exception:
            logger.exception("phone-code sign-in failed elapsed=%.3fs", time.perf_counter() - started)
            raise
        logger.info("phone-code sign-in completed elapsed=%.3fs", time.perf_counter() - started)

    async def load_dialog_tabs(self) -> list[DialogTab]:
        """Return built-in views plus the user's Telegram chat folders."""
        tabs = [
            DialogTab("all", "Chats", "all"),
            DialogTab("private", "Private", "private"),
            DialogTab("groups", "Groups", "groups"),
        ]
        try:
            filters = await self.client(functions.messages.GetDialogFiltersRequest())
        except Exception:
            logger.exception("dialog folder load failed")
            self.dialog_tabs = tabs
            self._custom_filters = {}
            return tabs
        self._custom_filters = {}
        for item in filters:
            if not isinstance(item, (types.DialogFilter, types.DialogFilterChatlist)):
                continue
            title = getattr(getattr(item, "title", None), "text", None) or str(getattr(item, "title", "Folder"))
            key = f"folder:{item.id}"
            tabs.append(DialogTab(key, title, "folder", item))
            self._custom_filters[key] = item
        self.dialog_tabs = tabs
        return tabs

    async def load_dialogs(self, limit: int = 60, tab: str = "all") -> list[Dialog]:
        started = time.perf_counter()
        logger.info("dialog load started limit=%s connected=%s", limit, self.client.is_connected())
        try:
            loaded = [
                Dialog(
                    id=dialog.id,
                    title=dialog.name or "(ohne Titel)",
                    unread=dialog.unread_count,
                    entity=dialog.entity,
                    preview=(dialog.message.message if dialog.message else "") or ("[media]" if dialog.message and dialog.message.media else ""),
                    preview_date=dialog.message.date if dialog.message else None,
                    is_private=dialog.is_user,
                    is_group=dialog.is_group,
                    is_channel=dialog.is_channel,
                )
                async for dialog in self.client.iter_dialogs(limit=limit)
                if dialog.is_user or dialog.is_group or dialog.is_channel
            ]
            self.dialogs = self._filter_dialogs(loaded, tab)
            logger.info("dialog load completed count=%s elapsed=%.3fs", len(self.dialogs), time.perf_counter() - started)
            return self.dialogs
        except Exception:
            logger.exception("dialog load failed limit=%s elapsed=%.3fs", limit, time.perf_counter() - started)
            raise

    def _filter_dialogs(self, dialogs: list[Dialog], tab: str) -> list[Dialog]:
        if tab == "private":
            return [dialog for dialog in dialogs if dialog.is_private]
        if tab == "groups":
            return [dialog for dialog in dialogs if dialog.is_group]
        folder = getattr(self, "_custom_filters", {}).get(tab)
        if folder is None:
            return dialogs
        include = {utils.get_peer_id(peer) for peer in getattr(folder, "include_peers", [])}
        exclude = {utils.get_peer_id(peer) for peer in getattr(folder, "exclude_peers", [])}
        filtered = []
        for dialog in dialogs:
            peer_id = utils.get_peer_id(dialog.entity)
            if include and peer_id not in include:
                continue
            if peer_id in exclude:
                continue
            if getattr(folder, "groups", False) and not dialog.is_group:
                continue
            if getattr(folder, "broadcasts", False) and not dialog.is_channel:
                continue
            filtered.append(dialog)
        return filtered

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
    def _valid_image(target: VerifiedDir, name: str) -> bool:
        """Validate ``name`` within ``target``, opened by dir_fd (not by path).

        Re-opening by a freshly-joined path here would re-resolve ``name``
        from scratch, giving up the identity guarantee ``target`` carries;
        opening it relative to ``target.fd`` keeps validation pinned to the
        exact file whose existence/size was already checked through the
        same fd.
        """
        try:
            with target.open_binary(name) as handle, Image.open(handle) as image:
                image.verify()
            return True
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError):
            return False

    @classmethod
    def _usable_gif_image(cls, target: VerifiedDir, name: str) -> bool:
        """Reject corrupt, degenerate, and effectively-black Telegram GIF frames.

        The size floor only guards against stub/placeholder images (e.g. a
        1x1 icon); Telegram's embedded document thumbnail is frequently
        smaller than a typical video poster frame and is otherwise a
        perfectly usable preview once scaled for the terminal.
        """
        if not cls._valid_image(target, name):
            return False
        try:
            with target.open_binary(name) as handle, Image.open(handle) as image:
                if image.width < 24 or image.height < 24:
                    logger.debug(
                        "gif frame rejected too small name=%s size=%sx%s",
                        name, image.width, image.height,
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
                        "gif frame rejected too dark name=%s mean=%.1f max=%s",
                        name, mean, maximum,
                    )
                    return False
                return True
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError):
            return False

    @staticmethod
    def _declared_size(item: object) -> int | None:
        return getattr(getattr(item, "file", None), "size", None)

    @staticmethod
    def _size_guard(cap: int):
        """A Telethon progress_callback that aborts once bytes read exceed cap.

        Declared file sizes come from message metadata and are checked before
        starting a download; this callback is defense-in-depth in case that
        declared size is missing or wrong.
        """

        def guard(current: int, _total: int) -> None:
            if current > cap:
                raise MediaTooLargeError(f"download exceeded {cap} bytes (at {current})")

        return guard

    @staticmethod
    def _cached_names_for(target: VerifiedDir, message_id: int) -> list[str]:
        """Names in ``target`` that look like a finished cache entry for ``message_id``.

        Mirrors the old ``target.glob(f"{message_id}.*")`` intent: a literal
        ``"<message_id>."`` prefix (not just any name containing the digits),
        excluding hidden/temp files and empty ones.
        """
        prefix = f"{message_id}."
        return [
            name for name in target.list_names()
            if name.startswith(prefix) and not name.startswith(".") and not name.endswith(".tmp")
            and target.is_file(name) and target.size(name) > 0
        ]

    async def _cache_media(self, dialog_id: int, item: object) -> Path | None:
        # ponytail: serialize full downloads; use per-message locks if parallel playback is needed.
        async with self._media_download_lock:
            return await self._download_media(dialog_id, item)

    async def _download_media(self, dialog_id: int, item: object) -> Path | None:
        started = time.perf_counter()
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        with self.media_cache.subdir(str(dialog_id)) as target:
            media_kind = self._media_kind(item)
            fallback_extension = ".mp4" if media_kind in {"gif", "video"} else ".jpg" if media_kind == "photo" else ""
            extension = safe_extension(
                getattr(getattr(item, "file", None), "ext", None), fallback=fallback_extension
            )

            declared_size = self._declared_size(item)
            if declared_size is not None and declared_size > MAX_MEDIA_BYTES:
                logger.warning(
                    "media download rejected: declared size exceeds cap dialog_id=%s message_id=%s size=%s cap=%s",
                    dialog_id, message_id, declared_size, MAX_MEDIA_BYTES,
                )
                return None

            requested_name = f"{message_id}{extension}"
            temp_name = f".{message_id}.tmp-{uuid.uuid4().hex}{extension}"
            has_existing = target.is_file(requested_name)
            if has_existing and target.size(requested_name) > 0:
                path = target.path / requested_name
                logger.debug("media cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, path)
                return path

            cached_alternatives = self._cached_names_for(target, message_id)
            if cached_alternatives:
                path = target.path / cached_alternatives[0]
                logger.debug("media cache hit alternative dialog_id=%s message_id=%s path=%s", dialog_id, message_id, path)
                return path

            if has_existing:
                logger.warning("invalid media cache removed dialog_id=%s message_id=%s path=%s", dialog_id, message_id, target.path / requested_name)
                target.remove(requested_name)
            target.remove(temp_name)
            try:
                logger.info("media download started dialog_id=%s message_id=%s kind=%s", dialog_id, message_id, media_kind)
                # Telethon manages this file itself via its own path-based
                # I/O, so it can't be handed our dir_fd directly; the random
                # uuid4 component in temp_name makes the resulting brief
                # plain-path window practically unracable. Everything around
                # it -- the existence/size checks above, the publish-via-
                # rename below, and cleanup -- stays fully dir_fd-anchored.
                downloaded = await asyncio.wait_for(
                    self.client.download_media(
                        item, file=str(target.path / temp_name), progress_callback=self._size_guard(MAX_MEDIA_BYTES)
                    ),
                    timeout=90,
                )
                valid = bool(downloaded) and target.is_file(temp_name) and target.size(temp_name) > 0
                final_size = target.size(temp_name) if valid else 0
                if valid:
                    target.replace(temp_name, requested_name)
                logger.info(
                    "media download completed dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                    dialog_id,
                    message_id,
                    valid,
                    final_size,
                    time.perf_counter() - started,
                )
                return target.path / requested_name if valid else None
            except asyncio.CancelledError:
                logger.warning("media download cancelled dialog_id=%s message_id=%s", dialog_id, message_id)
                raise
            except MediaTooLargeError:
                logger.warning(
                    "media download aborted: exceeded size cap dialog_id=%s message_id=%s cap=%s",
                    dialog_id, message_id, MAX_MEDIA_BYTES,
                )
                return None
            except Exception:
                # A failed preview must never make the whole chat unreadable.
                logger.exception("media download failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
                return None
            finally:
                target.remove(temp_name)

    async def cache_media_for_message(self, dialog_id: int, item: object) -> Path | None:
        """Download a media message on demand, e.g. before opening it in mpv."""
        return await self._cache_media(dialog_id, item)

    async def _cache_media_thumbnail(self, dialog_id: int, item: object) -> Path | None:
        if self._media_kind(item) == "gif":
            return await self._cache_gif_thumbnail(dialog_id, item)
        started = time.perf_counter()
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        with self.media_cache.subpath(str(dialog_id), "thumbs") as target:
            requested_name = f"{message_id}.jpg"
            temp_stem = f".{message_id}.tmp-{uuid.uuid4().hex}"
            temp_name = f"{temp_stem}.jpg"
            temp_name_mp4 = f"{temp_stem}.mp4"
            has_existing = target.is_file(requested_name)
            is_video = self._media_kind(item) == "video"
            valid_existing = (
                self._usable_gif_image(target, requested_name)
                if is_video and has_existing
                else self._valid_image(target, requested_name)
                if has_existing
                else False
            )
            if has_existing and target.size(requested_name) > 0 and valid_existing:
                path = target.path / requested_name
                logger.debug("thumbnail cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, path)
                return path
            if has_existing:
                logger.warning("invalid thumbnail cache removed dialog_id=%s message_id=%s path=%s", dialog_id, message_id, target.path / requested_name)
                target.remove(requested_name)
            target.remove(temp_name)
            try:
                logger.info("thumbnail download started dialog_id=%s message_id=%s kind=%s", dialog_id, message_id, self._media_kind(item))
                # See _cache_media(): Telethon writes this file itself via a
                # plain path, so the fd-anchored guarantee resumes right
                # after -- the validity check and publish-via-rename below
                # both go through target's dir_fd, not a fresh path lookup.
                downloaded = await asyncio.wait_for(
                    self.client.download_media(
                        item,
                        file=str(target.path / temp_name),
                        thumb=-1,
                        progress_callback=self._size_guard(MAX_THUMBNAIL_BYTES),
                    ),
                    timeout=25,
                )
                valid = bool(
                    downloaded
                    and target.is_file(temp_name)
                    and target.size(temp_name) > 0
                    and (self._usable_gif_image(target, temp_name) if is_video else self._valid_image(target, temp_name))
                )
                final_size = target.size(temp_name) if valid else 0
                if valid:
                    target.replace(temp_name, requested_name)
                logger.info(
                    "thumbnail download completed dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                    dialog_id,
                    message_id,
                    valid,
                    final_size,
                    time.perf_counter() - started,
                )
                return target.path / requested_name if valid else None
            except asyncio.CancelledError:
                logger.warning("thumbnail download cancelled dialog_id=%s message_id=%s", dialog_id, message_id)
                raise
            except Exception:
                logger.exception("thumbnail download failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
                return None
            finally:
                target.remove(temp_name)
                target.remove(temp_name_mp4)

    async def _cache_gif_thumbnail(self, dialog_id: int, item: object) -> Path | None:
        """Return a validated GIF preview without exposing partial downloads."""
        started = time.perf_counter()
        message_id = getattr(item, "id", None)
        if message_id is None:
            return None
        with (
            self.media_cache.subdir(str(dialog_id)) as media_dir,
            media_dir.subdir("thumbs") as target,
        ):
            output_name = f"{message_id}.jpg"
            temp_stem = f".{message_id}.tmp-{uuid.uuid4().hex}"
            fallback_name = f".{message_id}.fallback-{uuid.uuid4().hex}.jpg"

            if self._usable_gif_image(target, output_name):
                path = target.path / output_name
                logger.debug("gif thumbnail cache hit dialog_id=%s message_id=%s path=%s", dialog_id, message_id, path)
                return path
            target.remove(output_name)

            # Never let an old interrupted transfer become the next render source.
            target.remove(fallback_name)

            # Telegram's embedded GIF thumbnail is cheap and prevents a selected
            # chat from showing only [gif] while the full MP4 is being fetched.
            try:
                logger.info("gif embedded thumbnail download started dialog_id=%s message_id=%s", dialog_id, message_id)
                downloaded = await asyncio.wait_for(
                    self.client.download_media(
                        item,
                        file=str(target.path / fallback_name),
                        thumb=-1,
                        progress_callback=self._size_guard(MAX_THUMBNAIL_BYTES),
                    ),
                    timeout=25,
                )
                candidate_present = bool(downloaded) and target.is_file(fallback_name)
                if candidate_present and self._usable_gif_image(target, fallback_name):
                    target.replace(fallback_name, output_name)
                    logger.info(
                        "gif embedded thumbnail used dialog_id=%s message_id=%s bytes=%s elapsed=%.3fs",
                        dialog_id, message_id, target.size(output_name), time.perf_counter() - started,
                    )
                    return target.path / output_name
                elif candidate_present:
                    logger.info(
                        "gif embedded thumbnail rejected (unusable/black) dialog_id=%s message_id=%s bytes=%s",
                        dialog_id, message_id, target.size(fallback_name),
                    )
                    target.remove(fallback_name)
            except asyncio.CancelledError:
                target.remove(fallback_name)
                raise
            except (asyncio.TimeoutError, OSError) as exc:
                logger.warning(
                    "gif embedded thumbnail timed out dialog_id=%s message_id=%s error=%s elapsed=%.3fs",
                    dialog_id, message_id, str(exc) or type(exc).__name__, time.perf_counter() - started,
                )
                target.remove(fallback_name)
            except Exception:
                target.remove(fallback_name)
                logger.exception("gif embedded thumbnail failed dialog_id=%s message_id=%s", dialog_id, message_id)
            finally:
                target.remove(fallback_name)

            # If embedded thumbnail was missing or black, check for a cached full video
            # or download it if ffmpeg is available to extract a usable frame.
            cached_names = sorted(self._cached_names_for(media_dir, message_id))
            source = media_dir.path / cached_names[0] if cached_names else None
            ffmpeg = resolve_trusted_binary("ffmpeg")

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

            def extract() -> bool:
                # ffmpeg opens its output-frame pattern itself (a third-party
                # process, not our own I/O), so it necessarily writes by
                # plain path like Telethon's downloads above; the random
                # uuid4 stem keeps that brief window unracable. Selecting,
                # validating, publishing and cleaning up the resulting
                # frames all go through target's dir_fd below.
                frame_prefix_path = target.path / temp_stem
                frame_pattern = Path(f"{frame_prefix_path}-%03d.jpg")
                frame_prefix = f"{temp_stem}-"
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
                        env=safe_subprocess_env(),
                    )
                    frame_names = sorted(
                        name for name in target.list_names()
                        if name.startswith(frame_prefix) and name.endswith(".jpg")
                    )
                    for name in frame_names:
                        if self._usable_gif_image(target, name):
                            target.replace(name, output_name)
                            for leftover in frame_names:
                                target.remove(leftover)
                            return True
                    for name in frame_names:
                        target.remove(name)
                    return False
                except (OSError, subprocess.SubprocessError):
                    for name in target.list_names():
                        if name.startswith(frame_prefix) and name.endswith(".jpg"):
                            target.remove(name)
                    return False

            extracted = await asyncio.to_thread(extract)
            result = (
                target.path / output_name
                if extracted and target.is_file(output_name) and target.size(output_name) > 0
                and self._usable_gif_image(target, output_name)
                else None
            )
            if extracted:
                logger.info(
                    "gif thumbnail extracted dialog_id=%s message_id=%s valid=%s bytes=%s elapsed=%.3fs",
                    dialog_id, message_id, bool(result), target.size(output_name) if result else 0,
                    time.perf_counter() - started,
                )
            else:
                logger.warning("gif frame extraction failed dialog_id=%s message_id=%s elapsed=%.3fs", dialog_id, message_id, time.perf_counter() - started)
            if extracted and result is None:
                target.remove(output_name)
            target.remove(fallback_name)
            return result

    async def _cache_youtube_thumbnail(self, url: str) -> Path | None:
        started = time.perf_counter()
        match = YOUTUBE_ID_RE.search(url)
        if not match:
            return None
        video_id = match.group(1).split("&", 1)[0]
        with self.media_cache.subdir("youtube") as target:
            name = f"{hashlib.sha256(video_id.encode()).hexdigest()[:20]}.jpg"
            if target.is_file(name) and self._valid_image(target, name):
                path = target.path / name
                logger.debug("youtube thumbnail cache hit video_id=%s path=%s", video_id, path)
                return path
            temp_name = f".{name}.tmp-{uuid.uuid4().hex}"

            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            def download() -> bool:
                # Our own code end to end (unlike Telethon/ffmpeg elsewhere),
                # so the write itself goes through target's dir_fd too, not
                # just the surrounding checks.
                try:
                    request = Request(thumbnail_url, headers={"User-Agent": "omagram/0.1"})
                    with urlopen(request, timeout=5) as response:
                        declared_length = response.headers.get("Content-Length")
                        if declared_length is not None:
                            try:
                                if int(declared_length) > MAX_YOUTUBE_THUMBNAIL_BYTES:
                                    logger.warning(
                                        "youtube thumbnail rejected: declared length exceeds cap video_id=%s length=%s cap=%s",
                                        video_id, declared_length, MAX_YOUTUBE_THUMBNAIL_BYTES,
                                    )
                                    return False
                            except ValueError:
                                pass
                        written = 0
                        with os.fdopen(target.open_write_stream(temp_name), "wb") as handle:
                            while True:
                                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > MAX_YOUTUBE_THUMBNAIL_BYTES:
                                    logger.warning(
                                        "youtube thumbnail aborted: exceeded cap mid-transfer video_id=%s cap=%s",
                                        video_id, MAX_YOUTUBE_THUMBNAIL_BYTES,
                                    )
                                    return False
                                handle.write(chunk)
                    if self._valid_image(target, temp_name):
                        target.replace(temp_name, name)
                        return True
                    return False
                except Exception:
                    return False
                finally:
                    target.remove(temp_name)

            succeeded = await asyncio.to_thread(download)
            result = target.path / name if succeeded else None
            logger.info(
                "youtube thumbnail completed video_id=%s valid=%s elapsed=%.3fs",
                video_id,
                result is not None,
                time.perf_counter() - started,
            )
            return result

    async def send_message(self, dialog: Dialog, text: str) -> None:
        await self.client.send_message(dialog.entity, text)

    async def send_file(
        self, dialog: Dialog, path: Path, caption: str = "", progress_callback=None
    ) -> None:
        """Upload an explicitly selected local file, preserving its name and bytes."""
        path = Path(path).expanduser()
        if len(caption.encode("utf-16-le")) // 2 > 1024:
            raise ValueError("File caption must be at most 1024 characters")
        # Nonblocking open also lets us reject FIFOs without freezing the UI.
        with open(path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Select a regular file")
            if not 0 < info.st_size <= MAX_MEDIA_BYTES:
                raise ValueError("File must be nonempty and at most 2 GiB")
            await self.client.send_file(
                dialog.entity, handle, file_size=info.st_size,
                caption=caption, parse_mode=None, force_document=True,
                attributes=[types.DocumentAttributeFilename(path.name)],
                progress_callback=progress_callback,
            )
