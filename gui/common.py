"""gui/common.py - Shared helpers: the app name, album labels, page headings,
profiles, the app's own settings file (gui.json) and small table helpers."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLabel, QMenu, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .i18n import tr
from .style import STATUS, TIER_LABELS


HERE = Path(__file__).resolve().parent.parent       # the repo root (the engine modules)
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import plan as planmod  # noqa: E402,F401  (pages import it from here)
import profiles as pf  # noqa: E402
import reasons  # noqa: E402,F401  (pages import it from here)

APP = "TagMender"


def notify(win, setting, title, body="") -> bool:
    """Tell you something finished while TagMender is in the background, when this computer's
    settings say so (app_settings notifications.finished or .watch): the window asks for
    attention, and the desktop shows a notification where notify-send is installed. True
    when told."""
    import shutil
    import subprocess
    import app_settings
    try:
        if not app_settings.get(f"notifications.{setting}", True):
            return False
    except Exception:
        return False
    if win is None or win.isActiveWindow():
        return False
    from PySide6.QtWidgets import QApplication
    QApplication.alert(win, 0)
    tool = shutil.which("notify-send")
    if tool and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        try:
            subprocess.Popen([tool, "--app-name", APP, title, body], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass
    return True


def label_for(album):
    """'Ignored' / 'Skipped' for excluded albums (never 'Unresolved'), else the status word."""
    st = album["status"]
    if st in ("excluded", "skipped") and album.get("label"):
        return STATUS[st][0], album["label"]
    sym, word = STATUS.get(st, ("·", st.title()))[:2]
    word = tr(word)
    # how far identification got, beside the status: an album whose pressing, or release, is not
    # proven says so ("Ready · Partial")
    tier = (album.get("identification") or {}).get("tier")
    if st not in ("unresolved", "excluded", "skipped") and tier in TIER_LABELS:
        word = f"{word} · {tr(TIER_LABELS[tier])}"
    return sym, word


# the order a summary lists statuses in: what can be applied first, then what needs a look
ORDER = ("ready", "unresolved", "blocked", "conflict", "failed", "done", "unchanged", "excluded", "skipped")


def plural(n, word, many=None) -> str:
    """'1 file', '11 files'."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def counts_text(counts) -> str:
    """{"ready": 3, "unchanged": 1} as the status column says it: "3 Ready, 1 Up to date"."""
    keys = sorted(counts, key=lambda k: (ORDER.index(k) if k in ORDER else len(ORDER), k))
    return ", ".join(f"{counts[k]} {tr(STATUS[k][1]) if k in STATUS else k.replace('_', ' ').capitalize()}"
                     for k in keys if counts[k])


def counts_by_label(albums) -> str:
    """The summary as the rows say it: "3 Ready, 1 Ignored, 1 Skipped" (an excluded album is
    counted by its label, as label_for names it; a tier such as "Partial" is not split out)."""
    counts, order = {}, {}
    for a in albums:
        st = a["status"]
        word = a["label"] if st in ("excluded", "skipped") and a.get("label") else \
            (tr(STATUS[st][1]) if st in STATUS else st.replace("_", " ").capitalize())
        counts[word] = counts.get(word, 0) + 1
        order.setdefault(word, ORDER.index(st) if st in ORDER else len(ORDER))
    return ", ".join(f"{n} {w}" for w, n in sorted(counts.items(), key=lambda x: (order[x[0]], x[0])))


def album_words(path, root=None) -> str:
    """An album folder as every list names it: its own name first, then the folder it is in -
    unless that folder is `root` (a watched folder: "Moss — inbox" would read as an artist)."""
    p = Path(str(path))
    if root is not None and os.path.normpath(str(p.parent)) == os.path.normpath(str(root)):
        return p.name or str(path)
    return f"{p.name} — {p.parent.name}" if p.parent.name else (p.name or str(path))


def music_paths(mime) -> list:
    """The dropped folders and audio files (an image, a PDF or a web link is not music)."""
    out = []
    for u in mime.urls() if mime is not None and mime.hasUrls() else []:
        if not u.isLocalFile():
            continue
        p = u.toLocalFile()
        if os.path.splitext(p)[1].lower() in ex.AUDIO_EXT or os.path.isdir(p):
            out.append(p)
    return out


def heading(text, hint=""):
    box = QVBoxLayout()
    box.setSpacing(4)
    t = QLabel(text)
    t.setProperty("role", "title")
    t.setAccessibleName(text)
    box.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setProperty("role", "hint")
        h.setWordWrap(True)
        box.addWidget(h)
    return box


def separator():
    """A hairline between sections (instead of a box)."""
    f = QFrame()
    f.setProperty("role", "separator")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedHeight(1)
    return f


def section(text):
    """A section headline with a hairline above it."""
    box = QVBoxLayout()
    box.setContentsMargins(0, 8, 0, 0)
    box.setSpacing(6)
    box.addWidget(separator())
    t = QLabel(text)
    t.setProperty("role", "headline")
    t.setAccessibleName(text)
    box.addWidget(t)
    return box


def filter_table(table, text):
    """Hide the rows of a QTableWidget that do not hold text (any column, any case)."""
    text = (text or "").casefold()
    for r in range(table.rowCount()):
        cells = (table.item(r, c) for c in range(table.columnCount()))
        table.setRowHidden(r, bool(text) and not any(it and (text in it.text().casefold() or
                                                            text in it.toolTip().casefold()) for it in cells))


def profile_names():
    return ["default"] + [n for n in pf.names()]


def load_profile(name):
    return pf.default() if name == "default" else pf.load(name)


def open_folder(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def search_web(album):
    """The system browser, searching for the album (no embedded browser)."""
    ident = album.get("identification") or {}
    rel = album.get("release") or {}
    words = " ".join(filter(None, [(rel.get("albumartist") or [""])[0], (rel.get("album") or [""])[0]])) \
        or Path(album["source_dir"]).name + " " + Path(album["source_dir"]).parent.name
    QDesktopServices.openUrl(QUrl("https://duckduckgo.com/?q=" + urllib.parse.quote(words + " album")))
    return ident


def watch_does(p) -> str:
    """What a profile does with an album dropped into one of its watched folders."""
    dest = p["destination"] or "?"
    return {"move": tr("Moved into {dest}").format(dest=dest),
            "copy": tr("Copied into {dest} (the drop stays)").format(dest=dest)}.get(
        p["operation"], tr("Tagged and renamed where it is (not moved)"))


def music_folder() -> str:
    """Where folder choosers start: Preferences › General › Music folder, else ~/Music (else
    the home folder)."""
    try:
        import app_settings
        chosen = os.path.expanduser(str(app_settings.get("library_folder") or ""))
    except (OSError, ValueError):
        chosen = ""
    if chosen and os.path.isdir(chosen):
        return chosen
    music = Path.home() / "Music"
    return str(music if music.is_dir() else Path.home())


def _gui_prefs() -> dict:
    try:
        return json.loads((ex.CONFIG_DIR / "gui.json").read_text())
    except (OSError, ValueError):
        return {}


def _set_gui_pref(key, value):
    d = _gui_prefs()
    d[key] = value
    try:
        ex.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ex.CONFIG_DIR / ".gui.json.tmp"
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, ex.CONFIG_DIR / "gui.json")
    except OSError:
        pass


# reduced motion: Preferences, else the desktop's "enable-animations", read once off the UI thread
_desktop_animations = None


def read_desktop_motion():
    """Ask the desktop (gsettings) on a plain thread; the answer is kept for reduced_motion()."""
    import threading

    def ask():
        global _desktop_animations
        import subprocess
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            _desktop_animations = out != "false"
        except (OSError, subprocess.SubprocessError):
            _desktop_animations = True
    threading.Thread(target=ask, daemon=True).start()


def reduced_motion() -> bool:
    v = _gui_prefs().get("reduce_motion")
    if v is not None:
        return bool(v)
    return _desktop_animations is False


def left_header(view):
    """Column titles over the start of their column, as the cells are (Qt centres them)."""
    view.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)


class MiddleElide(QStyledItemDelegate):
    """A column of paths: shortened in the middle, so the start and the folder's own name both
    stay in view (prose columns are shortened at the end, so they read from the start)."""

    PAD = 16                                         # (the cell's padding, both sides: style.py)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        width = option.rect.width() - self.PAD - (option.decorationSize.width() + 4 if not option.icon.isNull() else 0)
        if width > 0 and option.fontMetrics.horizontalAdvance(option.text) > width:
            option.text = option.fontMetrics.elidedText(option.text, Qt.ElideMiddle, width)


def path_column(view, col):
    d = MiddleElide(view)
    view.setItemDelegateForColumn(col, d)
    view._path_delegates = getattr(view, "_path_delegates", []) + [d]       # (kept alive with the view)


def say(lb, key, text):
    """A label whose text changes: a screen reader gets "Key: the text" (a fixed accessible name
    would be read instead of the text, and the text never)."""
    lb.setText(text)
    plain = re.sub(r"<[^>]+>", "", text or "").strip()
    lb.setAccessibleName(f"{key}: {plain}" if plain else key)


def popup_menu(parent, view, pos, actions):
    """A table's context menu, for the mouse and for the Menu key or Shift+F10 (whose position
    does not reliably land on the current row): the row under pos, else the current row."""
    if not view.indexAt(pos).isValid():
        cur = view.currentIndex()
        if not (cur.isValid() and view.selectionModel() is not None and view.selectionModel().hasSelection()):
            return None
        pos = view.visualRect(cur).center()
    m = QMenu(parent)
    for a in actions:
        if a is None:
            m.addSeparator()
        else:
            m.addAction(a)
    from .actions import mac_menu
    mac_menu(m)
    m.exec(view.viewport().mapToGlobal(pos))
    return m


def _table(cols, stretch=0, fit=()):
    """A list as every page shows one: rows, no grid, alternate row shading, no edits."""
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels([tr(c) for c in cols])
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setTextElideMode(Qt.ElideRight)
    left_header(t)
    t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    for c in fit:
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().hide()
    return t


def _fill(table, rows):
    table.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            table.setItem(i, j, QTableWidgetItem(str(v)))


def status_cell(it, key, word, icon=None):
    """A status cell: a Lucide icon, the word and the colour of a style.STATUS key (the icon set
    the inspector uses; never a typed glyph, whose weight depends on the font installed)."""
    from . import icons, style
    it.setText(word)
    it.setIcon(icons.icon(icon or style.status_icon(key), key, 14))
    it.setForeground(style.status_color(key))
    it.setData(Qt.AccessibleTextRole, word)
    return it


def folder_item(path, album=False, root=None):
    """A table cell that names a folder by its last part (an album: its name, then its
    parent's unless that is `root`), the whole path in its tooltip and behind Qt.UserRole (what
    the page acts on)."""
    path = str(path)
    it = QTableWidgetItem(album_words(path, root) if album else (Path(path).name or path))
    it.setToolTip(path)
    it.setData(Qt.UserRole, path)
    return it
