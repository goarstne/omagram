from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import subprocess
import tomllib
from functools import partial
from pathlib import Path

from PIL import Image as PilImage, ImageColor
from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Link, ListItem, ListView, Select, Static
from textual.widget import Widget
from textual.worker import Worker, WorkerState
from textual_image._terminal import CellSize, get_cell_size
# Unicode is the fallback; Foot uses native Sixel widgets below.

from . import __version__
from .client import Dialog, DialogTab, Message, TelegramBackend
from textual_image.widget import HalfcellImage as TerminalImage, TGPImage

from .native_media import fit_image_widget, sixel_widget
from .security import resolve_trusted_binary, safe_subprocess_env


logger = logging.getLogger("omagram.ui")

CONNECT_TIMEOUT = 20
AUTH_TIMEOUT = 10
DIALOG_TIMEOUT = 20
MEDIA_PREVIEW_TIMEOUT = 120
URL_RE = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)

# Textual owns stdin once the app starts, so textual-image's interactive cell
# size query can stall on the first render. Omarchy terminals use the standard
# VT cell geometry; cache it before Textual starts.
setattr(get_cell_size, "_result", CellSize(10, 20))

THEME_PATH = Path(
    os.environ.get(
        "OMARCHY_THEME_COLORS",
        Path.home() / ".local/state/omarchy/current/theme/colors.toml",
    )
)

DEFAULT_PALETTE: dict[str, str] = {
    "background": "#05182e",
    "dark_background": "#031222",
    "darker_background": "#020c17",
    "lighter_background": "#0a2540",
    "foreground": "#f6dcac",
    "bright_foreground": "#f6dcac",
    "light_foreground": "#a7c9c6",
    "dark_foreground": "#3f8f8a",
    "muted": "#2a6b78",
    "selection": "#134e5a",
    "accent": "#faa968",
    "green": "#028391",
    "cyan": "#8cbfb8",
    "blue": "#3f8f8a",
    "red": "#f85525",
    "yellow": "#e97b3c",
}


def load_theme_palette() -> dict[str, str]:
    palette = dict(DEFAULT_PALETTE)
    try:
        if THEME_PATH.is_file():
            with open(THEME_PATH, "rb") as f:
                data = tomllib.load(f)
            for k, v in data.items():
                if isinstance(v, str) and v.startswith("#"):
                    palette[k] = v
    except Exception:
        pass
    return palette


def build_css(p: dict[str, str]) -> str:
    # Keep colors as Textual variables so refresh_css() can apply a changed
    # Omarchy palette to the already-mounted widgets.
    p = {key: f"$om-{key}" for key in p}
    return f"""
    Screen {{
        background: {p["background"]};
        color: {p["foreground"]};
    }}

    Header {{
        background: {p["dark_background"]};
        color: {p["bright_foreground"]};
        height: 1;
    }}

    HeaderTitle {{
        color: {p["accent"]};
        text-style: bold;
    }}

    HeaderClock {{
        color: {p["muted"]};
        text-style: bold;
    }}

    Footer {{
        background: {p["dark_background"]};
        color: {p["light_foreground"]};
        height: 1;
    }}

    FooterKey {{
        background: {p["selection"]};
        color: {p["bright_foreground"]};
        text-style: bold;
    }}

    FooterDescription {{
        color: {p["light_foreground"]};
        margin-right: 1;
    }}

    #layout {{
        height: 1fr;
        background: {p["background"]};
    }}

    #sidebar {{
        width: 30%;
        min-width: 20;
        max-width: 36;
        height: 100%;
        background: {p["dark_background"]};
        border-right: heavy {p["selection"]};
    }}

    .-compact #sidebar {{
        width: 20;
        min-width: 16;
    }}

    #sidebar.hidden {{
        display: none;
    }}

    #sidebar-title {{
        width: 100%;
        height: 2;
        padding: 0 1;
        background: {p["dark_background"]};
        color: {p["light_foreground"]};
        text-style: bold;
        border-bottom: double {p["selection"]};
    }}

    #dialog-tabs-wrap {{
        width: 100%;
        height: 2;
        color: {p["bright_foreground"]};
        background: {p["lighter_background"]};
        border-bottom: double {p["selection"]};
        padding: 0 1;
        align: left middle;
    }}

    #dialog-tabs-label {{
        width: 100%;
        height: 1;
        padding: 0 1;
        color: {p["bright_foreground"]};
        text-style: bold;
    }}

    #dialog-tabs {{
        display: none;
    }}

    #dialogs {{
        height: 1fr;
        background: transparent;
        padding: 0;
        scrollbar-size-vertical: 1;
        scrollbar-color: {p["selection"]};
        scrollbar-color-hover: {p["muted"]};
    }}

    ListItem {{
        height: auto;
        min-height: 1;
        padding: 0 1;
        background: transparent;
        color: {p["light_foreground"]};
    }}

    ListItem:hover {{
        background: {p["lighter_background"]};
    }}

    ListItem.--highlight {{
        background: {p["lighter_background"]};
        color: {p["bright_foreground"]};
        border-left: heavy {p["muted"]};
    }}

    #dialogs:focus ListItem.--highlight {{
        background: {p["selection"]};
        color: {p["bright_foreground"]};
        border-left: heavy {p["accent"]};
    }}

    #chat {{
        width: 1fr;
        height: 100%;
        background: {p["background"]};
    }}

    #chat-title {{
        height: 2;
        padding: 0 2;
        background: {p["dark_background"]};
        color: {p["bright_foreground"]};
        border-bottom: double {p["selection"]};
        content-align: left middle;
    }}

    #messages-scroll {{
        height: 1fr;
        padding: 1 2;
        background: {p["background"]};
        scrollbar-size-vertical: 1;
        scrollbar-color: {p["selection"]};
        scrollbar-color-hover: {p["muted"]};
    }}

    #messages {{
        height: auto;
        background: transparent;
        color: {p["foreground"]};
    }}

    #status {{
        height: 1;
        padding: 0 2;
        background: {p["dark_background"]};
        color: {p["muted"]};
        border-top: solid {p["darker_background"]};
    }}

    #status.error {{
        color: {p["red"]};
        text-style: bold;
    }}

    #composer {{
        height: 3;
        margin: 0 1 1 1;
        padding: 0 1;
        background: {p["darker_background"]};
        color: {p["bright_foreground"]};
        border: heavy {p["selection"]};
    }}

    #composer:focus {{
        border: heavy {p["accent"]};
    }}
    """


def prepare_terminal_image(path: Path, background: str) -> PilImage.Image:
    """Return an RGB frame so transparent Telegram media cannot become black."""
    with PilImage.open(path) as source:
        rgba = source.convert("RGBA")
    backdrop = PilImage.new("RGBA", rgba.size, ImageColor.getrgb(background) + (255,))
    return PilImage.alpha_composite(backdrop, rgba).convert("RGB")


def _terminal_emulator() -> str:
    """Detect the actual terminal behind xdg-terminal-exec/foot wrappers."""
    declared = os.environ.get("TERM_PROGRAM", "").lower()
    if declared:
        return declared
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    pid = os.getpid()
    for _ in range(8):
        try:
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
            command = Path(f"/proc/{ppid}/comm").read_text().strip().lower()
        except (FileNotFoundError, OSError, ValueError, IndexError):
            break
        if command in {"foot", "ghostty", "kitty", "wezterm"}:
            return command
        pid = ppid
    return ""


def terminal_image(image: PilImage.Image, height: int = 20):
    """Use native terminal graphics and degrade gracefully when unavailable."""
    mode = os.environ.get("OMAGRAM_IMAGE_MODE", "auto").lower()
    emulator = _terminal_emulator()
    if mode == "halfcell":
        image_class = TerminalImage
    elif mode == "sixel" or (mode == "auto" and emulator == "foot"):
        return sixel_widget(image, max_height=height)
    elif mode == "tgp" or (mode == "auto" and emulator in {"ghostty", "kitty", "wezterm"}):
        image_class = TGPImage
    else:
        image_class = TerminalImage
    return fit_image_widget(image_class(image), image, height)


# Hand-built block-font wordmark, matching the width Omarchy uses for its own
# ANSI screensaver banner. Kept ASCII/box-drawing only so it survives any
# terminal font, unlike a Nerd Font icon.
OMAGRAM_LOGO = "\n".join([
    "  ██████    ██      ██    ██████      ██████    ████████      ██████    ██      ██",
    "██      ██  ████  ████  ██      ██  ██          ██      ██  ██      ██  ████  ████",
    "██      ██  ██  ██  ██  ██████████  ██  ██████  ████████    ██████████  ██  ██  ██",
    "██      ██  ██      ██  ██      ██  ██      ██  ██  ██      ██      ██  ██      ██",
    "  ██████    ██      ██  ██      ██    ██████    ██    ██    ██      ██  ██      ██",
])


class InfoScreen(ModalScreen[None]):
    """Hidden `i` easter egg: a BBS-style boot/about screen. Not a menu item."""

    DEFAULT_CSS = """
    InfoScreen {
        align: center middle;
        background: $om-darker_background 60%;
    }

    #info-card {
        width: auto;
        max-width: 96;
        height: auto;
        padding: 1 3;
        background: $om-dark_background;
        border: double $om-accent;
    }

    #info-logo {
        width: auto;
        color: $om-accent;
        text-style: bold;
    }

    #info-tagline {
        width: 100%;
        content-align: center middle;
        color: $om-light_foreground;
        margin-top: 1;
    }

    #info-meta {
        width: 100%;
        content-align: center middle;
        color: $om-muted;
    }

    #info-hint {
        width: 100%;
        content-align: center middle;
        color: $om-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="info-card"):
            yield Static(OMAGRAM_LOGO, id="info-logo", markup=False)
            yield Static(
                "── demoscene-grade Telegram TUI for Linux & Omarchy ──", id="info-tagline"
            )
            yield Static(f"v{__version__} · code: @goarstne · github.com/goarstne/omagram", id="info-meta")
            yield Static("press any key to return", id="info-hint")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()


def media_label(message: Message) -> str:
    name = message.media_name or message.youtube_url or message.media_kind or "Media"
    size = f" · {message.media_size:,} bytes" if message.media_size is not None else ""
    return f"{name}{size}"


class UploadScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
    DEFAULT_CSS = """
    UploadScreen, MediaScreen { align: center middle; background: $om-darker_background 80%; }
    .media-card { width: 72; max-width: 100%; height: auto; max-height: 100%;
        padding: 1 2; border: solid $om-accent; background: $om-dark_background; }
    .media-card Horizontal { height: auto; }
    #upload-status { height: auto; min-height: 2; }
    #media-options { height: 12; }
    """

    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog
        self.worker = None
        self._cancel_requested = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="media-card"):
            yield Static(f"Send document to: {self.dialog.title}", markup=False)
            yield Label("Local file (1 byte–2 GiB)")
            yield Input(placeholder="/path/to/file", id="upload-path")
            yield Label("Caption (optional, up to 1024 characters)")
            yield Input(id="upload-caption")
            with Horizontal(id="upload-actions"):
                yield Button("Browse…", id="upload-browse")
            yield Static("", id="upload-status", markup=False)
            with Horizontal():
                yield Button("Send", id="upload-send", variant="primary")
                yield Button("Cancel", id="upload-cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only the explicit Send button may send a file.
        event.stop()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "upload-browse":
            await self._choose_file()
            return
        if event.button.id == "upload-cancel":
            await self.action_cancel()
        elif event.button.id == "upload-send" and self.worker is None:
            try:
                value = self.query_one("#upload-path", Input).value
                if not value:
                    await self._choose_file()
                    value = self.query_one("#upload-path", Input).value
                if not value:
                    raise ValueError("No file selected")
                path = Path(value).expanduser().absolute()
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 2 * 1024**3:
                    raise ValueError("Choose a regular file between 1 byte and 2 GiB")
            except (OSError, ValueError) as exc:
                self.query_one("#upload-status", Static).update(f"Upload failed: {exc}")
                return
            caption = self.query_one("#upload-caption", Input).value
            self.query_one("#upload-send", Button).disabled = True
            for field in self.query(Input):
                field.disabled = True
            self._cancel_requested = False
            self.worker = self.run_worker(partial(self._upload, path, caption), group="upload", exit_on_error=False)

    async def _choose_file(self) -> None:
        picker = resolve_trusted_binary("omarchy-file-select")
        if not picker:
            self.query_one("#upload-status", Static).update("Upload failed: no file picker is available")
            return
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [picker, "--title", "Send file"],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                env=safe_subprocess_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.query_one("#upload-status", Static).update(f"File picker failed: {exc}")
            return
        if result.returncode == 0 and result.stdout.strip():
            self.query_one("#upload-path", Input).value = result.stdout.strip().splitlines()[0]
        elif result.returncode not in (1,):
            self.query_one("#upload-status", Static).update("File picker failed")

    async def action_cancel(self) -> None:
        if self.worker is not None:
            worker = self.worker
            self._cancel_requested = True
            # Textual 8.2.8 needs the task to enter _run before cancellation.
            await asyncio.sleep(0)
            if self.worker is worker:
                worker.cancel()
        else:
            self.dismiss()

    async def _upload(self, path: Path, caption: str) -> None:
        if self._cancel_requested:
            raise asyncio.CancelledError
        status = self.query_one("#upload-status", Static)
        status.update("Uploading… 0%")

        def progress(current: int, total: int) -> None:
            percent = min(100, current * 100 // total) if total else 0
            status.update(f"Uploading… {percent}% · {current:,}/{total:,} bytes")

        try:
            await self.app.backend.send_file(self.dialog, path, caption, progress_callback=progress)
        except Exception as exc:
            status.update(f"Upload failed: {exc}")
        else:
            self.dismiss()
            self.app.run_worker(self.app._after_send(self.dialog), group="sent-refresh", exit_on_error=False)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is self.worker and event.worker.is_finished:
            self.worker = None
            if event.state == WorkerState.CANCELLED:
                self.query_one("#upload-status", Static).update(
                    "Upload cancelled; delivery may be uncertain. Check the chat before retrying."
                )
            self.query_one("#upload-send", Button).disabled = False
            for field in self.query(Input):
                field.disabled = False


class MediaScreen(ModalScreen[Message | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = UploadScreen.DEFAULT_CSS

    def __init__(self, messages: list[Message]):
        super().__init__()
        self.messages = messages

    def compose(self) -> ComposeResult:
        with Vertical(classes="media-card"):
            yield Label("Choose media · Enter to open · Esc to cancel")
            yield ListView(*(ListItem(Label(
                f"{m.timestamp:%H:%M} · {media_label(m)}", markup=False
            )) for m in self.messages), id="media-options")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if event.list_view.index is not None:
            self.dismiss(self.messages[event.list_view.index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class MessagePanel(Vertical):
    """Batched transcript containing real widgets, not a nested scrolling log."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: list[Widget] = []
        self._pending = False
        self._force_end = False

    def clear(self, *, scroll_to_end=False):
        self._items = []
        self._force_end |= scroll_to_end
        self._schedule()

    def write(self, content):
        self._items.append(content if isinstance(content, Widget) else Static(content, markup=True))
        self._schedule()

    def _schedule(self):
        if not self._pending:
            self._pending = True
            self.call_after_refresh(self._flush)

    async def _flush(self):
        items = list(self._items)
        scroll = self.parent
        previous_y = scroll.scroll_y
        follow_end = self._force_end or scroll.is_vertical_scroll_end
        self._force_end = False
        try:
            obsolete = [child for child in self.children if child not in items]
            if obsolete:
                await self.remove_children(obsolete)
            new = [child for child in items if child.parent is not self]
            if new:
                await self.mount(*new)
        finally:
            self._pending = False
        if items != self._items:
            self._schedule()
        elif follow_end:
            self.call_after_refresh(scroll.scroll_end, animate=False)
        else:
            self.call_after_refresh(scroll.scroll_to, y=previous_y, animate=False)


class DialogItem(ListItem):
    def __init__(self, dialog: Dialog, label: str):
        super().__init__(Label(label))
        self.dialog = dialog


class ChatListView(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("o", "select_cursor", "Open", show=False),
    ]


class TelegramTui(App[None]):
    TITLE = "omagram"
    SUB_TITLE = "Telegram · Omarchy"

    CSS = build_css(load_theme_palette())

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "reload", "Reload", show=True),
        Binding("c", "compose", "Write", show=True),
        Binding("v", "choose_media", "Media", show=True),
        Binding("s", "upload", "Send file", show=True),
        Binding("t", "cycle_tab", "Next tab", show=True),
        Binding("b", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False),
        Binding("escape", "focus_chats", "Chats", show=True),
        Binding("ctrl+c", "copy_selection", "Copy", show=False),
        Binding("i", "show_info", "Info", show=False),
    ]

    def __init__(self, backend: TelegramBackend):
        self.palette = load_theme_palette()
        self.CSS = build_css(self.palette)
        super().__init__()
        self.backend = backend
        self.selected: Dialog | None = None
        self._current_messages: list[Message] = []
        self._chat_generation = 0
        self._rendered_dialog_id: int | None = None
        self._play_worker = None
        self._sending = False
        self._dialogs_lock = asyncio.Lock()
        self._theme_signature: tuple[int, int] | None = None
        self._dialog_tabs: list[DialogTab] = list(getattr(backend, "dialog_tabs", []))
        self._active_tab = "all"

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update({f"om-{key}": value for key, value in self.palette.items()})
        return variables

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static("CHATS  [t]", id="dialog-tabs-label", markup=False)
                yield Select(
                    [("Chats", "all"), ("Private", "private"), ("Groups", "groups")],
                    value="all", allow_blank=False, id="dialog-tabs"
                )
                yield ChatListView(id="dialogs")
            with Vertical(id="chat"):
                yield Static("[dim]Select a chat · j/k to navigate · Enter to open[/dim]", id="chat-title")
                with VerticalScroll(id="messages-scroll"):
                    yield MessagePanel(id="messages")
                yield Static("", id="status")
                yield Input(placeholder="Write a message… (Enter to send · Esc for chats)", id="composer")
        yield Footer()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 70, "-compact")

    async def on_mount(self) -> None:
        logger.info(
            "ui mounted size=%s terminal=%s image_mode=%s",
            self.size,
            _terminal_emulator() or "unknown",
            os.environ.get("OMAGRAM_IMAGE_MODE", "auto"),
        )
        self.backend.on_new_message = self._message_event
        self._set_tab_options(self._dialog_tabs)
        self.query_one("#messages", MessagePanel).write("[dim]Connecting to Telegram…[/dim]")
        self._theme_signature = self._current_theme_signature()
        self.set_interval(1.0, self._watch_theme)
        self.run_worker(self._connect(), exclusive=True)

    def _current_theme_signature(self) -> tuple[int, int] | None:
        try:
            stat = THEME_PATH.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _watch_theme(self) -> None:
        signature = self._current_theme_signature()
        if signature is None or signature == self._theme_signature:
            return
        self._theme_signature = signature
        logger.info("theme file changed path=%s signature=%s", THEME_PATH, signature)
        self.palette = load_theme_palette()
        self.CSS = build_css(self.palette)
        self.refresh_css()
        self._set_tab_options(self._dialog_tabs)
        if self.selected:
            self._update_chat_title(self.selected)
            self.run_worker(self._load_messages(self.selected), exclusive=True)
        self.run_worker(self._reload_dialogs(), exclusive=True)
        self._set_status("Theme updated")

    async def _connect(self) -> None:
        started = asyncio.get_running_loop().time()
        try:
            logger.info("connect worker started")
            self._set_status("Connecting to Telegram…")
            if self.backend.client.is_connected():
                logger.info("connect worker: backend already connected; skipping connect")
            else:
                await asyncio.wait_for(self.backend.connect(), timeout=CONNECT_TIMEOUT)
            logger.info("connect worker: transport connected elapsed=%.3fs", asyncio.get_running_loop().time() - started)
            if not await asyncio.wait_for(self.backend.authorized(), timeout=AUTH_TIMEOUT):
                logger.warning("connect worker: session is not authorized")
                self._set_status("Not authorized. Run: omagram auth", error=True)
                return
            logger.info("connect worker: session authorized")
            if hasattr(self.backend, "load_dialog_tabs"):
                self._set_tab_options(await self.backend.load_dialog_tabs())
            await asyncio.wait_for(self._reload_dialogs(), timeout=DIALOG_TIMEOUT)
            logger.info("connect worker: dialogs rendered count=%s", len(self.backend.dialogs))
            self._set_status("Online · j/k navigate · Enter open · c write")
            self.query_one("#dialogs", ChatListView).focus()
        except asyncio.TimeoutError:
            elapsed = asyncio.get_running_loop().time() - started
            logger.exception("connect worker timed out after %.3fs", elapsed)
            try:
                await asyncio.wait_for(self.backend.disconnect(), timeout=3)
            except Exception:
                logger.exception("cleanup after connect timeout failed")
            self._set_status(f"Error: Telegram connection timed out ({CONNECT_TIMEOUT}s)", error=True)
        except Exception as exc:
            logger.exception("connect worker failed after %.3fs", asyncio.get_running_loop().time() - started)
            self._set_status(f"Error: {exc}", error=True)

    async def _reload_dialogs(self) -> None:
        async with self._dialogs_lock:
            started = asyncio.get_running_loop().time()
            logger.info("ui dialog reload started backend_count=%s", len(self.backend.dialogs))
            view = self.query_one("#dialogs", ChatListView)
            try:
                dialogs = list(await self.backend.load_dialogs(tab=self._active_tab))
            except TypeError:
                dialogs = list(await self.backend.load_dialogs())
            highlighted = view.highlighted_child
            keep_id = highlighted.dialog.id if isinstance(highlighted, DialogItem) else (
                self.selected.id if self.selected else None
            )
            await view.clear()
            total_unread = 0
            for dialog in dialogs:
                total_unread += dialog.unread
                preview = " ".join(dialog.preview.replace("\n", " ").split())
                if len(preview) > 54:
                    preview = preview[:51].rstrip() + "…"
                if dialog.unread:
                    label_text = (
                        f"[bold {self.palette['bright_foreground']}]{escape(dialog.title)}[/]  "
                        f"[bold {self.palette['accent']}][{dialog.unread}][/]"
                    )
                else:
                    label_text = f"[{self.palette['light_foreground']}]{escape(dialog.title)}[/]"
                if preview:
                    label_text += f"\n[dim {self.palette['muted']}]{escape(preview)}[/]"
                await view.append(DialogItem(dialog, label_text))

            if view.children:
                view.index = next((i for i, dialog in enumerate(dialogs) if dialog.id == keep_id), 0)

            self.query_one("#dialog-tabs", Select).tooltip = (
                f"{total_unread} unread" if total_unread else f"{len(dialogs)} chats"
            )
            logger.info("ui dialog reload completed count=%s elapsed=%.3fs", len(dialogs), asyncio.get_running_loop().time() - started)

    async def action_reload(self) -> None:
        logger.info("manual reload requested connected=%s", self.backend.client.is_connected())
        try:
            if not self.backend.client.is_connected():
                self._set_status("Retrying Telegram connection…")
                await asyncio.wait_for(self.backend.connect(), timeout=CONNECT_TIMEOUT)
            if not await self.backend.authorized():
                self._set_status("Not authorized. Run: omagram auth", error=True)
                return
            await asyncio.wait_for(self._reload_dialogs(), timeout=DIALOG_TIMEOUT)
            self._set_status("Chats reloaded")
        except asyncio.TimeoutError:
            logger.exception("manual reload timed out")
            try:
                await asyncio.wait_for(self.backend.disconnect(), timeout=3)
            except Exception:
                logger.exception("cleanup after reload timeout failed")
            self._set_status(f"Reload timed out ({DIALOG_TIMEOUT}s)", error=True)
        except Exception as exc:
            logger.exception("manual reload failed")
            self._set_status(f"Reload failed: {exc}", error=True)

    def action_focus_chats(self) -> None:
        self.query_one("#dialogs", ChatListView).focus()

    def _set_tab_options(self, tabs: list[DialogTab]) -> None:
        if not tabs:
            tabs = [
                DialogTab("all", "Chats", "all"),
                DialogTab("private", "Private", "private"),
                DialogTab("groups", "Groups", "groups"),
            ]
        self._dialog_tabs = tabs
        select = self.query_one("#dialog-tabs", Select)
        select.set_options([(tab.title, tab.key) for tab in tabs])
        select.value = self._active_tab if any(tab.key == self._active_tab for tab in tabs) else tabs[0].key
        active = next(tab for tab in tabs if tab.key == select.value)
        self.query_one("#dialog-tabs-label", Static).update(f"Folder · {active.title}  (t)")

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "dialog-tabs" or event.value == Select.BLANK:
            return
        if str(event.value) == self._active_tab:
            return
        self._active_tab = str(event.value)
        self._update_tab_button()
        self.run_worker(self._reload_dialogs(), group="dialog-tab", exclusive=True, exit_on_error=False)

    def _update_tab_button(self) -> None:
        title = next((tab.title for tab in self._dialog_tabs if tab.key == self._active_tab), self._active_tab)
        self.query_one("#dialog-tabs-label", Static).update(f"Folder · {title}  (t)")

    def action_cycle_tab(self) -> None:
        keys = [tab.key for tab in self._dialog_tabs]
        if not keys:
            return
        index = keys.index(self._active_tab) if self._active_tab in keys else -1
        self._active_tab = keys[(index + 1) % len(keys)]
        self.query_one("#dialog-tabs", Select).value = self._active_tab
        self._update_tab_button()

    def action_show_info(self) -> None:
        self.push_screen(InfoScreen())

    def action_copy_selection(self) -> None:
        """Copy Textual input selections; terminal selections use native copy mode."""
        selected = getattr(self.focused, "selected_text", "")
        if not selected:
            return
        try:
            copier = resolve_trusted_binary("wl-copy")
            subprocess.run(
                [copier, "--trim-newline"],
                input=selected,
                text=True,
                check=True,
                env=safe_subprocess_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("clipboard copy failed: %s", exc)
            return
        self._set_status("Copied selection")

    def action_compose(self) -> None:
        if self.selected is None:
            item = self.query_one("#dialogs", ChatListView).highlighted_child
            if isinstance(item, DialogItem):
                self.selected = item.dialog
                self._update_chat_title(self.selected)
                self.run_worker(self._load_messages(self.selected), exclusive=True)
        self.query_one("#composer", Input).focus()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.toggle_class("hidden")
        if sidebar.has_class("hidden"):
            self.query_one("#composer", Input).focus()
        else:
            self.query_one("#dialogs", ChatListView).focus()

    def _update_chat_title(self, dialog: Dialog) -> None:
        title = escape(dialog.title)
        badge = (
            f"  [bold {self.palette['accent']}][{dialog.unread} unread][/]"
            if dialog.unread
            else ""
        )
        self.query_one("#chat-title", Static).update(
            f"[bold {self.palette['bright_foreground']}]# {title}[/]{badge}"
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "dialogs":
            return
        if not isinstance(event.item, DialogItem):
            return
        self.selected = event.item.dialog
        self._update_chat_title(self.selected)
        self.run_worker(self._load_messages(self.selected), exclusive=True)

    async def _load_messages(self, dialog: Dialog) -> None:
        self._chat_generation += 1
        generation = self._chat_generation
        started = asyncio.get_running_loop().time()
        logger.info("chat load started generation=%s dialog_id=%s", generation, dialog.id)
        try:
            messages = await self.backend.messages(dialog)
            if generation != self._chat_generation or self.selected not in (None, dialog):
                logger.info("chat load discarded generation=%s dialog_id=%s", generation, dialog.id)
                return
            self._current_messages = messages
            self._render_messages(messages)
            logger.info("chat text rendered generation=%s dialog_id=%s messages=%s elapsed=%.3fs", generation, dialog.id, len(messages), asyncio.get_running_loop().time() - started)
            if hasattr(self.backend, "load_message_preview") and any(
                message.media_kind or message.youtube_url for message in messages
            ):
                self.run_worker(
                    self._hydrate_media(dialog, messages, generation),
                    # Scope exclusivity to this dialog: switching to a
                    # different chat must not cancel another chat's in-flight
                    # GIF/photo downloads. A shared group name previously
                    # meant every chat switch killed whatever preview was
                    # downloading, so slow media (esp. GIF thumbnails) never
                    # got the chance to finish and be cached.
                    group=f"media-previews-{dialog.id}",
                    exclusive=True,
                    exit_on_error=False,
                )
        except Exception as exc:
            logger.exception("chat load failed generation=%s dialog_id=%s", generation, dialog.id)
            if generation != self._chat_generation or self.selected not in (None, dialog):
                return
            panel = self.query_one("#messages", MessagePanel)
            panel.clear()
            panel.write(f"[bold {self.palette['red']}]Error loading messages: {escape(str(exc))}[/]")

    def _render_messages(self, messages: list[Message]) -> None:
        panel = self.query_one("#messages", MessagePanel)
        dialog_id = self.selected.id if self.selected else None
        panel.clear(scroll_to_end=dialog_id != self._rendered_dialog_id)
        self._rendered_dialog_id = dialog_id
        logger.debug(
            "render messages total=%s media_paths=%s gifs=%s missing_previews=%s",
            len(messages),
            sum(bool(message.media_path) for message in messages),
            sum(message.media_kind == "gif" for message in messages),
            sum(bool(message.media_kind) and not message.media_path for message in messages),
        )
        if not messages:
            panel.write(f"[{self.palette['muted']}](no messages)[/]")
            return
        previous: Message | None = None
        for message in messages:
            local_day = message.timestamp.astimezone().date()
            if previous is None or local_day != previous.timestamp.astimezone().date():
                panel.write(self._format_date_separator(local_day))
                grouped = False
            else:
                gap = (message.timestamp - previous.timestamp).total_seconds()
                grouped = (
                    previous.outgoing == message.outgoing
                    and previous.sender == message.sender
                    and 0 <= gap <= 300
                )
            has_links = bool(self._extract_urls(message.text))
            panel.write(self._format_message(message, grouped=grouped, link_text=not has_links))
            for url in self._extract_urls(message.text):
                panel.write(Link(url, url=url, classes="message-link"))
            previous = message
            if message.media_path:
                try:
                    panel.write(
                        terminal_image(
                            prepare_terminal_image(message.media_path, self.palette["background"]),
                            height=20,
                        )
                    )
                except Exception:
                    logger.exception(
                        "media render failed path=%s kind=%s",
                        message.media_path,
                        message.media_kind,
                    )
                    panel.write(f"[{self.palette['red']}]Media preview unavailable[/]")
            if message.media_kind:
                panel.write(f"[{self.palette['accent']}]{escape(media_label(message))}[/] · v to choose media")
            if message.youtube_url:
                if message.youtube_thumbnail_path:
                    try:
                        panel.write(
                            terminal_image(
                                prepare_terminal_image(
                                    message.youtube_thumbnail_path,
                                    self.palette["background"],
                                ),
                                height=10,
                            )
                        )
                    except Exception:
                        pass
                panel.write(
                    f"[{self.palette['accent']}]▶ YouTube[/]  "
                    f"[{self.palette['muted']}]press v to play in mpv[/]\n"
                    f"[{self.palette['light_foreground']}]{escape(message.youtube_url)}[/]"
                )

    async def _hydrate_media(
        self, dialog: Dialog, messages: list[Message], generation: int
    ) -> None:
        started = asyncio.get_running_loop().time()
        logger.info("media hydration started generation=%s dialog_id=%s messages=%s", generation, dialog.id, len(messages))
        preview_start = max(0, len(messages) - 24)
        semaphore = asyncio.Semaphore(4)
        failures = 0
        changed = False
        last_render = 0.0

        async def hydrate_one(index: int, message: Message) -> None:
            nonlocal failures, changed, last_render
            enabled = index >= preview_start or message.media_kind == "gif"
            if not enabled or not (message.media_kind or message.youtube_url):
                return
            try:
                async with semaphore:
                    result = await asyncio.wait_for(
                        self.backend.load_message_preview(dialog.id, message, True),
                        timeout=MEDIA_PREVIEW_TIMEOUT,
                    )
                if not isinstance(result, tuple):
                    return
                media_path, youtube_url, youtube_thumbnail_path = result
                if message.media_kind in {"photo", "gif", "video"} and media_path is None:
                    failures += 1
                    logger.warning(
                        "media preview unavailable dialog_id=%s message_id=%s kind=%s",
                        dialog.id,
                        getattr(message.source, "id", None),
                        message.media_kind,
                    )
                if media_path == message.media_path and youtube_thumbnail_path == message.youtube_thumbnail_path:
                    return
                message.media_path = media_path
                message.youtube_url = youtube_url or message.youtube_url
                message.youtube_thumbnail_path = youtube_thumbnail_path
                changed = True
                now = asyncio.get_running_loop().time()
                if (
                    now - last_render >= 0.25
                    and generation == self._chat_generation
                    and self.selected in (None, dialog)
                ):
                    last_render = now
                    self._render_messages(messages)
                    logger.debug("media preview rendered incrementally dialog_id=%s message_id=%s", dialog.id, getattr(message.source, "id", None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                logger.warning(
                    "media preview failed dialog_id=%s message_id=%s error=%s",
                    dialog.id,
                    getattr(message.source, "id", None),
                    exc,
                )

        try:
            await asyncio.gather(*(hydrate_one(index, message) for index, message in enumerate(messages)))
        except asyncio.CancelledError:
            logger.info("media hydration cancelled generation=%s dialog_id=%s", generation, dialog.id)
            raise
        if generation != self._chat_generation or self.selected not in (None, dialog):
            logger.info("media hydration discarded generation=%s dialog_id=%s", generation, dialog.id)
            return
        if changed:
            self._render_messages(messages)
        logger.info("media hydration completed generation=%s dialog_id=%s failures=%s elapsed=%.3fs", generation, dialog.id, failures, asyncio.get_running_loop().time() - started)

    def action_upload(self) -> None:
        if any(isinstance(screen, UploadScreen) for screen in self.screen_stack):
            return
        if not self.selected:
            self._set_status("Select a chat before uploading", error=True)
            return
        self.push_screen(UploadScreen(self.selected))

    def action_choose_media(self) -> None:
        dialog = self.selected
        media = [m for m in reversed(self._current_messages) if m.media_kind or m.youtube_url]
        if not dialog or not media:
            self._set_status("No media in this chat", error=True)
            return

        def chosen(message: Message | None) -> None:
            if message is not None:
                self._start_media(dialog.id, message)

        self.push_screen(MediaScreen(media), chosen)

    def _start_media(self, dialog_id: int, message: Message) -> None:
        if self._play_worker is not None and not self._play_worker.is_finished:
            self._set_status("Media download already running…")
            return
        self._play_worker = self.run_worker(
            self._play_media(dialog_id, message),
            group="play-media", exclusive=True, exit_on_error=False,
        )

    async def action_play_media(self) -> None:
        """Play the newest YouTube or Telegram video in an external player."""
        if self._play_worker is not None and not self._play_worker.is_finished:
            self._set_status("Media download already running…")
            return
        playable = [m for m in self._current_messages if m.youtube_url or m.media_kind in {"video", "gif"}]
        if not playable or not self.selected:
            self._set_status("No playable media in this chat", error=True)
            return
        self._start_media(self.selected.id, playable[-1])

    async def _play_media(self, dialog_id: int, media: Message) -> None:
        url = media.youtube_url
        if not url:
            self._set_status(
                "Downloading media…"
            )
            try:
                path = await self.backend.cache_media_for_message(dialog_id, media.source)
            except Exception as exc:
                self._set_status(f"Media download failed: {exc}", error=True)
                return
            if not path:
                self._set_status("Could not download media", error=True)
                return
            url = str(path)

        if not url:
            self._set_status("No playable YouTube link in this chat", error=True)
            return
        player = resolve_trusted_binary("mpv") if media.media_kind != "file" else None
        opener = player or resolve_trusted_binary("xdg-open")
        if not opener:
            self._set_status("No media player (mpv/xdg-open) found", error=True)
            return
        try:
            command = [opener, "--force-window=yes"] if player else [opener]
            if player and media.media_kind == "gif":
                command.extend(["--loop-file=inf", "--no-audio"])
            command.append(url)
            subprocess.Popen(command, start_new_session=True, env=safe_subprocess_env())
            self._set_status("Opening media…")
        except OSError as exc:
            self._set_status(f"Could not open media: {exc}", error=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "composer" or self._sending:
            return
        text = event.value.strip()
        dialog = self.selected
        if not text or not dialog:
            return
        self._sending = True
        try:
            await self.backend.send_message(dialog, text)
        except Exception as exc:
            self._set_status(f"Send failed: {exc}", error=True)
        else:
            if self.selected == dialog and event.input.value == event.value:
                event.input.value = ""
            await self._after_send(dialog)
        finally:
            self._sending = False

    async def _after_send(self, dialog: Dialog) -> None:
        self._set_status(f"Sent to {dialog.title}")
        try:
            if self.selected and self.selected.id == dialog.id:
                await self._load_messages(self.selected)
            await self._reload_dialogs()
        except Exception as exc:
            self._set_status(f"Sent to {dialog.title}; refresh failed: {exc}", error=True)

    def _schedule_live_message_refresh(self, chat_id: int) -> None:
        """Enter Textual's event loop before touching widgets.

        Telethon dispatches updates from its own task context. Calling
        query_one()/ListView methods directly there can fail with Textual's
        ``active_app`` ContextVar lookup error.
        """
        self.run_worker(
            self._refresh_live_message(chat_id),
            group="live-message-refresh",
            exclusive=True,
            exit_on_error=False,
        )

    async def _message_event(self, chat_id: int) -> None:
        self.call_after_refresh(self._schedule_live_message_refresh, chat_id)

    async def _refresh_live_message(self, chat_id: int) -> None:
        logger.info("live message refresh started chat_id=%s selected_id=%s", chat_id, self.selected.id if self.selected else None)
        await self._reload_dialogs()
        if self.selected and self.selected.id == chat_id:
            await self._load_messages(self.selected)
        else:
            self._set_status("New message received · chat list updated")
        logger.info("live message refresh completed chat_id=%s", chat_id)

    def _format_date_separator(self, day) -> str:
        label = day.strftime("%a %d %b").upper()
        return f"[dim {self.palette['muted']}]── {label} ──[/]"

    def _format_message(self, message: Message, *, grouped: bool = False, link_text: bool = True) -> str:
        safe_text = self._format_links(message.text) if link_text else escape(URL_RE.sub("↗", message.text))
        if grouped:
            # Same sender, same day, within a few minutes of the previous
            # line: drop the repeated time/sender header and show a
            # continuation guide instead, like an IRC/BBS chat log.
            color = self.palette["foreground"] if message.outgoing else self.palette["light_foreground"]
            return f"      [dim {self.palette['muted']}]│[/] [{color}]{safe_text}[/]"
        clock = message.timestamp.astimezone().strftime("%H:%M")
        if message.outgoing:
            return (
                f"[dim {self.palette['muted']}]{clock}[/] "
                f"[bold {self.palette['accent']}]you[/] "
                f"[bold {self.palette['accent']}]›[/] "
                f"[{self.palette['foreground']}]{safe_text}[/]"
            )
        else:
            safe_sender = escape(message.sender)
            return (
                f"[dim {self.palette['muted']}]{clock}[/] "
                f"[bold {self.palette['cyan']}]{safe_sender}[/] "
                f"[dim {self.palette['muted']}]‹[/] "
                f"[{self.palette['light_foreground']}]{safe_text}[/]"
            )

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        urls = []
        for match in URL_RE.finditer(text):
            url = match.group(0)
            while url and url[-1] in ".,!?;:)":
                url = url[:-1]
            if url:
                urls.append(url)
        return urls

    @classmethod
    def _format_links(cls, text: str) -> str:
        """Escape chat text while emitting terminal-clickable OSC 8 links."""
        result: list[str] = []
        cursor = 0
        for match in URL_RE.finditer(text):
            result.append(escape(text[cursor:match.start()]))
            url = match.group(0)
            trailing = ""
            while url and url[-1] in ".,!?;:)":
                trailing = url[-1] + trailing
                url = url[:-1]
            if url:
                result.append(f'[link="{url}"]{escape(url)}[/link]')
            result.append(escape(trailing))
            cursor = match.end()
        result.append(escape(text[cursor:]))
        return "".join(result)

    def _set_status(self, text: str, error: bool = False) -> None:
        logger.debug("status update error=%s text=%s", error, text)
        status_widget = self.query_one("#status", Static)
        is_err = error or text.lower().startswith(("error", "failed", "send failed"))
        if is_err:
            status_widget.add_class("error")
            status_widget.update(f"[bold {self.palette['red']}]✖ {escape(text)}[/]")
        else:
            status_widget.remove_class("error")
            status_widget.update(f"[{self.palette['muted']}]● {escape(text)}[/]")
