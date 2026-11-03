"""gui/icons.py - Lucide line icons (ISC licence, assets/icons/lucide/), tinted from the palette.

icon(name, role, size) gives a QIcon drawn in the role's colour (style.color: ink, muted, accent,
on_accent, a status key ...) at 1x and 2x, with a faint disabled state. Icons are cached; style.apply
clears the cache when the colours change. A missing file gives an empty icon and one log line."""
from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

from . import style
from .common import HERE

DIR = HERE / "assets" / "icons" / "lucide"
NAMES = ("folder-plus", "folder-open", "eye", "check", "x", "search", "panel-right",
         "library", "triangle-alert", "history", "folder-sync", "list-checks", "sliders-horizontal",
         "circle-check", "circle-alert", "ban", "circle-x", "circle-dot", "loader-circle",
         "tag", "image", "info", "external-link", "globe", "disc-3", "refresh-cw", "undo-2", "rotate-ccw",
         "plus", "minus", "trash-2", "pencil", "settings-2", "key-round", "stethoscope", "download",
         "chevrons-up-down", "chevron-up", "chevron-down", "equal", "arrow-right", "sparkles")
_cache: dict = {}
_svg: dict = {}
_files = None                                   # (a temporary folder for the stylesheet's images)
log = logging.getLogger("tagmender.gui")


def clear():
    _cache.clear()


def _source(name):
    if name not in _svg:
        try:
            _svg[name] = (DIR / f"{name}.svg").read_text()
        except OSError:
            log.warning("icon %s is missing (%s)", name, DIR)
            _svg[name] = None
    return _svg[name]


def pixmap(name, colour, size=16, ratio=1.0, stroke=None) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer
    text = _source(name) if name else None               # (no name: a transparent square)
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.transparent)
    if not text:
        return px
    text = text.replace("currentColor", colour)
    if stroke:
        text = text.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    r = QSvgRenderer(QByteArray(text.encode()))
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name, role="ink", size=16) -> QIcon:
    key = (name, role, size, style.is_dark())
    if key in _cache:
        return _cache[key]
    if not _source(name):
        _cache[key] = QIcon()
        return _cache[key]
    ic = QIcon()
    for ratio in (1.0, 2.0):
        ic.addPixmap(pixmap(name, style.color(role), size, ratio), QIcon.Normal)
        ic.addPixmap(pixmap(name, style.color("faint"), size, ratio), QIcon.Disabled)
        ic.addPixmap(pixmap(name, style.color("on_selection"), size, ratio), QIcon.Selected)
    _cache[key] = ic
    return ic


def file(name, role="ink", size=16, stroke=None) -> str:
    """The icon in the role's colour as a PNG (drawn at 2x) for the stylesheet's url(...): check
    boxes and pop-up arrows. Written once to a temporary folder that is removed at exit."""
    global _files
    if _files is None:
        _files = Path(tempfile.mkdtemp(prefix="tagmender-icons-"))
        atexit.register(shutil.rmtree, _files, True)
    colour = style.color(role) if name else "none"
    p = _files / f"{name or 'blank'}-{colour.lstrip('#')}-{size}-{stroke or 2}.png"
    if not p.exists():
        pixmap(name, colour, size, 2.0, stroke).save(str(p), "PNG")
    return p.as_posix()


def size(n=16) -> QSize:
    return QSize(n, n)
