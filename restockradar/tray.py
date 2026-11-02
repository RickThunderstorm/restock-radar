"""Das Symbol neben der Uhr."""
from __future__ import annotations

import webbrowser
from typing import Callable

from PIL import Image, ImageDraw

from . import APP_NAME


def make_icon(size: int = 64) -> Image.Image:
    """Zeichnet das Radar-Symbol – auch für die .ico-Datei der .exe."""
    scale = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = lambda v: round(v * scale)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=s(16), fill=(17, 24, 39, 255))
    green, dim = (52, 211, 153, 255), (52, 211, 153, 110)
    d.ellipse((s(11), s(11), s(53), s(53)), outline=dim, width=max(1, s(3)))
    d.ellipse((s(20), s(20), s(44), s(44)), outline=(52, 211, 153, 170), width=max(1, s(3)))
    d.line((s(32), s(32), s(49), s(17)), fill=green, width=max(2, s(4)))
    d.ellipse((s(28), s(28), s(36), s(36)), fill=green)
    d.ellipse((s(38.5), s(35.5), s(47.5), s(44.5)), fill=(251, 191, 36, 255))
    return img


def run_tray(port: int, on_quit: Callable[[], None]) -> None:
    import pystray

    url = f"http://127.0.0.1:{port}/"

    def open_ui(icon=None, item=None):
        webbrowser.open(url)

    def quit_app(icon, item):
        on_quit()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"{APP_NAME} öffnen", open_ui, default=True),
        pystray.MenuItem("Beenden", quit_app),
    )
    pystray.Icon("RestockRadar", make_icon(64), f"{APP_NAME} – läuft", menu).run()
