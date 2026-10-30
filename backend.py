"""Read-only Telegram watcher; JSON lines connect it to Quickshell."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys


def selected_ids(value):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.isascii() or not item.isdigit()
        or not 0 < int(item) < 2**63 for item in value
    ):
        raise ValueError("Ungültige Kontaktauswahl")
    return sorted(set(value))


def contact_row(user, unread=0, messages=None):
    if getattr(user, "bot", False) or getattr(user, "is_self", False) or getattr(user, "deleted", False):
        return None
    return {
        "id": str(user.id),
        "name": " ".join(filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])) or getattr(user, "username", "") or "Unbenannt",
        "username": getattr(user, "username", "") or "",
        "unread": unread,
        "messages": messages or [],
    }


def format_time(dt):
    if not dt:
        return ""
    try:
        from datetime import datetime
        local_dt = dt.astimezone() if hasattr(dt, "astimezone") else dt
        now = datetime.now(local_dt.tzinfo) if hasattr(local_dt, "tzinfo") and local_dt.tzinfo else datetime.now()
        if hasattr(local_dt, "date") and local_dt.date() == now.date():
            return local_dt.strftime("%H:%M")
        elif hasattr(local_dt, "date") and (now.date() - local_dt.date()).days == 1:
            return f"Gestern {local_dt.strftime('%H:%M')}"
        return local_dt.strftime("%d.%m. %H:%M")
    except Exception:
        return ""


def format_message(msg):
    if isinstance(msg, dict):
        return {
            "id": msg.get("id", 0),
            "text": msg.get("text", msg.get("message", "")),
            "date": msg.get("date", ""),
            "out": msg.get("out", False),
        }

    raw_text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
    text = raw_text.strip()

    if not text:
        if getattr(msg, "photo", None):
            text = "[Foto]"
        elif getattr(msg, "voice", None):
            text = "[Sprachnachricht]"
        elif getattr(msg, "video_note", None):
            text = "[Videonachricht]"
        elif getattr(msg, "video", None):
            text = "[Video]"
        elif getattr(msg, "audio", None):
            text = "[Audiodatei]"
        elif getattr(msg, "sticker", None):
            emoji = getattr(getattr(msg, "file", None), "emoji", "")
            text = f"[Sticker {emoji}]" if emoji else "[Sticker]"
        elif getattr(msg, "gif", None):
            text = "[GIF]"
        elif getattr(msg, "poll", None):
            text = "[Umfrage]"
        elif getattr(msg, "document", None):
            file_name = getattr(getattr(msg, "file", None), "name", None)
            text = f"[Datei: {file_name}]" if file_name else "[Datei]"
        else:
            text = "[Nachricht]"
    elif getattr(msg, "photo", None):
        text = f"📷 {text}"
    elif getattr(msg, "video", None):
        text = f"📹 {text}"
    elif getattr(msg, "document", None):
        text = f"📎 {text}"

    return {
        "id": getattr(msg, "id", 0),
        "text": text,
        "date": format_time(getattr(msg, "date", None)),
        "out": getattr(msg, "out", False),
    }


def snapshot(contacts, selected, connected, error=""):
    rows = sorted(contacts.values(), key=lambda row: (
        row["id"] not in selected, -row["unread"] if row["id"] in selected else 0,
        row["name"].casefold(), row["id"],
    ))
    return {
        "connected": connected, "error": error, "contacts": rows,
        "selected": sorted(selected),
        "unread": sum(row["unread"] for row in rows if row["id"] in selected),
    }


def memory_session(path):
    from telethon.crypto import AuthKey
    from telethon.sessions import MemorySession
    from telegram_tui.config import validate_session_path

    validate_session_path(path)
    filename = path if path.name.endswith(".session") else path.with_name(path.name + ".session")
    if not filename.is_file():
        raise RuntimeError("Bitte zuerst in Omagram bei Telegram anmelden.")
    # Never let the watcher write or lock Omagram's session database.
    with contextlib.closing(sqlite3.connect(filename.absolute().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT dc_id, server_address, port, auth_key FROM sessions LIMIT 1").fetchone()
    if not row or not row[3]:
        raise RuntimeError("Bitte zuerst in Omagram bei Telegram anmelden.")
    session = MemorySession()
    session.set_dc(*row[:3])
    session.auth_key = AuthKey(row[3])
    return session


class Watcher:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.contacts = {}
        self.selected = set()
        self.connected = False
        self.error = "Verbindung wird hergestellt …"
        self.wake = asyncio.Event()
        self.stopped = False
        self.reload_contacts = True
        self.read_queue = asyncio.Queue()
        try:
            with config_dir.open_binary("contacts.json") as handle:
                self.selected = set(selected_ids(json.load(handle)))
        except FileNotFoundError:
            pass

    def publish(self):
        print(json.dumps(snapshot(self.contacts, self.selected, self.connected, self.error), ensure_ascii=True), flush=True)

    def select(self, value):
        ids = selected_ids(value)
        self.config_dir.write_atomic("contacts.json", json.dumps(ids).encode(), mode=0o600)
        self.selected = set(ids)
        self.publish()

    def toggle(self, value):
        item = selected_ids([value])[0]
        self.select(list(self.selected.symmetric_difference({item})))

    async def mark_read(self, contact_id):
        try:
            clean_id = selected_ids([contact_id])[0]
        except (ValueError, IndexError):
            return
        if clean_id in self.contacts:
            self.contacts[clean_id]["unread"] = 0
            self.contacts[clean_id]["messages"] = []
            self.publish()
        await self.read_queue.put(clean_id)
        self.wake.set()

    async def process_read_queue(self, client):
        while not self.read_queue.empty():
            uid = await self.read_queue.get()
            try:
                if hasattr(client, "send_read_acknowledge"):
                    await client.send_read_acknowledge(int(uid))
            except Exception as exc:
                logging.warning("Gelesen markieren fehlgeschlagen für %s: %s", uid, exc)

    async def commands(self):
        reader = asyncio.StreamReader(limit=65536)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        try:
            while line := await reader.readline():
                try:
                    command = json.loads(line)
                    if not isinstance(command, dict):
                        raise ValueError("Ungültiger Befehl")
                    if "toggle" in command:
                        self.toggle(command["toggle"])
                    elif "selected" in command:
                        self.select(command["selected"])
                    elif "read" in command or "mark_read" in command:
                        await self.mark_read(str(command.get("read") or command.get("mark_read")))
                    elif command.get("refresh"):
                        self.reload_contacts = True
                        self.wake.set()
                except (ValueError, OSError):
                    self.error = "Kontaktauswahl konnte nicht gespeichert werden."
                    self.publish()
        finally:
            transport.close()
            self.stopped = True
            self.wake.set()

    async def fetch_messages(self, client, entity, unread_count, fallback_message=None):
        if not hasattr(client, "get_messages"):
            return [format_message(fallback_message)] if fallback_message else []
        limit = min(max(unread_count, 1), 10)
        try:
            history = await client.get_messages(entity, limit=limit)
            return [format_message(m) for m in reversed(history)]
        except Exception as exc:
            logging.warning("Nachrichten laden fehlgeschlagen: %s", exc)
            return [format_message(fallback_message)] if fallback_message else []

    async def refresh(self, client):
        from telethon.tl import functions, types

        rows = {key: {**row, "unread": 0, "messages": []} for key, row in self.contacts.items()}
        if self.reload_contacts:
            result = await client(functions.contacts.GetContactsRequest(hash=0))
            rows = {}
            for user in result.users:
                row = contact_row(user)
                if row:
                    rows[row["id"]] = row
            self.reload_contacts = False
        # Archived chats (often spam) are hidden in the official apps; skip them here too.
        async for dialog in client.iter_dialogs(ignore_migrated=True, archived=False):
            if isinstance(dialog.entity, types.User):
                user_id = str(dialog.entity.id)
                unread = getattr(dialog, "unread_count", 0)
                messages = []
                if user_id in self.selected and unread > 0:
                    messages = await self.fetch_messages(
                        client, dialog.entity, unread, getattr(dialog, "message", None)
                    )
                row = contact_row(dialog.entity, unread, messages)
                if row:
                    rows[row["id"]] = row
        self.contacts = rows
        self.connected = True
        self.error = ""
        self.publish()

    async def run(self):
        from telethon import TelegramClient, events
        from telethon.tl import types
        from telegram_tui import config

        commands = asyncio.create_task(self.commands())
        self.publish()
        try:
            while not self.stopped:
                client = None
                try:
                    api_id, api_hash = config.load_config()
                    client = TelegramClient(
                        memory_session(config.SESSION_PATH), api_id, api_hash,
                        device_model="VIPgram", system_version="Linux", app_version="0.1.0",
                        timeout=10, connection_retries=2, request_retries=2,
                        flood_sleep_threshold=0,
                    )

                    @client.on(events.NewMessage(incoming=True))
                    async def incoming(event):
                        if event.is_private:
                            self.wake.set()

                    @client.on(events.Raw(types=[types.UpdateReadHistoryInbox, types.UpdateDeleteMessages]))
                    async def read_or_deleted(update):
                        self.wake.set()

                    async with asyncio.timeout(45):
                        await client.connect()
                        if not await client.is_user_authorized():
                            raise RuntimeError("Bitte zuerst in Omagram bei Telegram anmelden.")
                        await client.get_me()
                    self.reload_contacts = True
                    while not self.stopped:
                        self.wake.clear()
                        await self.process_read_queue(client)
                        async with asyncio.timeout(60):
                            await self.refresh(client)
                        # Poll as well: reads on another device may not produce an update here.
                        try:
                            await asyncio.wait_for(self.wake.wait(), timeout=30)
                        except TimeoutError:
                            pass
                except Exception as exc:
                    self.connected = False
                    self.error = str(exc) if type(exc) is RuntimeError else (
                        "Telegram nicht erreichbar (" + type(exc).__name__ + "). Neuer Versuch in 60 s."
                    )
                    self.publish()
                    self.wake.clear()
                    if not self.stopped:
                        try:
                            await asyncio.wait_for(self.wake.wait(), timeout=60)
                        except TimeoutError:
                            pass
                finally:
                    if client:
                        await client.disconnect()
        finally:
            commands.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await commands


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--omagram", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.omagram))
    from telegram_tui.security import open_verified_dir

    logging.basicConfig(level=logging.CRITICAL)
    config_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "vipgram"
    try:
        with open_verified_dir(config_path) as directory:
            asyncio.run(Watcher(directory).run())
    except (OSError, ValueError) as exc:
        print(json.dumps({"connected": False, "contacts": [], "selected": [], "unread": 0,
                          "error": "VIPgram-Konfiguration konnte nicht geladen werden (" + type(exc).__name__ + ")."}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
