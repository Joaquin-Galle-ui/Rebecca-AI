from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image


def build_tray_image(sprite_dir: Path | str, size: int = 64) -> Image.Image:
    sprite_dir = Path(sprite_dir)
    base_path = sprite_dir / "base_limpia.png"
    if not base_path.exists():
        base_path = sprite_dir / "base.png"
    portrait = Image.open(base_path).convert("RGBA")
    for layer_name in ("eyes_neutral.png", "mouth_closed.png"):
        layer = Image.open(sprite_dir / layer_name).convert("RGBA")
        portrait = Image.alpha_composite(portrait, layer)
    visible_bounds = portrait.getchannel("A").getbbox()
    if visible_bounds:
        portrait = portrait.crop(visible_bounds)
    portrait.thumbnail((size, size), Image.Resampling.LANCZOS)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.alpha_composite(
        portrait,
        ((size - portrait.width) // 2, (size - portrait.height) // 2),
    )
    return icon


class CompanionTray:
    def __init__(
        self,
        sprite_dir: Path | str,
        schedule: Callable[[Callable[[], None]], None],
        on_show: Callable[[], None],
        on_avatar: Callable[[], None],
        on_hide: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.sprite_dir = Path(sprite_dir)
        self.schedule = schedule
        self.on_show = on_show
        self.on_avatar = on_avatar
        self.on_hide = on_hide
        self.on_quit = on_quit
        self.icon: pystray.Icon | None = None
        self.thread: threading.Thread | None = None

    def _dispatch(self, action: Callable[[], None]):
        self.schedule(action)

    def start(self) -> None:
        if self.icon is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar Rebecca", lambda _icon, _item: self._dispatch(self.on_show), default=True),
            pystray.MenuItem("Mostrar solo avatar", lambda _icon, _item: self._dispatch(self.on_avatar)),
            pystray.MenuItem("Ocultar", lambda _icon, _item: self._dispatch(self.on_hide)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Cerrar Rebecca", lambda _icon, _item: self._dispatch(self.on_quit)),
        )
        self.icon = pystray.Icon(
            "RebeccaCompanion",
            build_tray_image(self.sprite_dir),
            "Rebecca Companion",
            menu,
        )
        self.thread = threading.Thread(target=self.icon.run, name="rebecca-tray", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            icon.stop()
