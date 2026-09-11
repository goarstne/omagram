"""Offline real-terminal probe: python -m telegram_tui.media_probe IMAGE."""

import sys

from PIL import Image
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from .native_media import cache_cell_geometry, sixel_widget


class MediaProbe(App):
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Static("Omagram native Sixel probe — q closes this test")
        with VerticalScroll():
            with Image.open(sys.argv[1]) as source:
                yield sixel_widget(source.convert("RGB"))
            yield Static("Native pixels above; no HalfCell renderer and no Telegram connection.")


if __name__ == "__main__":
    cache_cell_geometry()
    MediaProbe().run()
