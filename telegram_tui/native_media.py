"""Native image widgets; never insert Sixel Rich renderables into RichLog."""

import fcntl
import struct
import sys
import termios

from PIL import Image as PILImage
from rich.control import Control
from rich.segment import Segment
from textual_image._terminal import CellSize, get_cell_size
from textual_image.widget.sixel import Image as SixelImage, _ImageSixelImpl


class _ClippedSixel(_ImageSixelImpl):
    """Work around textual-image 0.13.2 cursor off-by-one (upstream #146)."""

    def _get_sixel_segments(self, sixel_data):
        segments = super()._get_sixel_segments(sixel_data)
        region = self.screen.find_widget(self).visible_region
        segments[-1] = Segment(
            Control.move_to(region.right, max(region.y, region.bottom - 1)).segment.text,
            style=segments[-1].style,
        )
        return segments


class NativeSixelImage(SixelImage, Renderable=SixelImage._Renderable):
    def compose(self):
        yield _ClippedSixel(self.image, self._sixel_options)


def cache_cell_geometry() -> CellSize:
    """Read kernel geometry without querying stdin owned by Textual."""
    try:
        rows, cols, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and cols and width and height:
            size = CellSize(max(1, width // cols), max(1, height // rows))
            setattr(get_cell_size, "_result", size)
            return size
    except (OSError, ValueError):
        pass
    size = getattr(get_cell_size, "_result", None) or CellSize(10, 20)
    setattr(get_cell_size, "_result", size)
    return size


def sixel_widget(image: PILImage.Image, max_height: int = 20) -> SixelImage:
    return fit_image_widget(NativeSixelImage(image), image, max_height)


def fit_image_widget(widget, image: PILImage.Image, max_height: int):
    """Fit all renderers proportionally, without enlarging thumbnails."""
    cell = cache_cell_geometry()
    # Never enlarge a small Telegram thumbnail to fill the whole transcript.
    scale = min(1, max_height * cell.height / image.height)
    width = max(1, round(image.width * scale / cell.width))
    # Sixel owns a child widget; other renderers need auto width on first layout.
    widget.styles.width = width if isinstance(widget, SixelImage) else "auto"
    widget.styles.max_width = "100%" if isinstance(widget, SixelImage) else width
    widget.styles.height = "auto"
    return widget
