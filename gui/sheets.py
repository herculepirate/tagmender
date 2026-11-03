"""gui/sheets.py - Window-modal sheets in place of app-modal message boxes.

A sheet belongs to one window: it is modal to that window only (Preferences stays usable), opens
centred under the window's toolbar (X11; Wayland places it itself) and fades in unless reduced
motion is on. Its buttons sit on the right with the default last, and the default names its
action ("Undo Job", "Apply 12 Albums"), never "OK".

The functions mirror Qt's static dialogs: warning, information, question -> bool, confirm -> bool,
ask -> the button chosen, get_text -> (text, ok), show_text, and the file pickers choose_folder ->
path and save_file -> path (window-modal QFileDialogs, never the app-modal static ones). On the
offscreen platform (tests, the screenshot tool) they never block: the call is recorded in `shown`
and the "no" answer returned. Patch them as gui.sheets.<name>."""
from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from . import icons, style
from .common import APP, reduced_motion
from .i18n import tr

shown: list = []                                         # (kind, headline, body) of sheets not shown


def _window(parent):
    return parent.window() if isinstance(parent, QWidget) else None


def blocking() -> bool:
    """False on the offscreen platform, where exec() would wait for ever."""
    return QGuiApplication.platformName() != "offscreen"


def prepare(dlg: QDialog, title=""):
    """Make any dialog a sheet of its parent's window: window-modal, a sheet window, faded in."""
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setWindowFlags((dlg.windowFlags() & ~Qt.WindowType_Mask) | Qt.Sheet)
    dlg.setWindowTitle(title or APP)
    dlg.setProperty("sheet", True)                       # (the stylesheet lifts it off the window)
    dlg._sheet = True
    return dlg


def place(dlg: QDialog):
    """Centre under the parent window's toolbar (X11 only; Wayland ignores positions)."""
    win = dlg.parentWidget().window() if dlg.parentWidget() else None
    if win is None or "wayland" in QGuiApplication.platformName():
        return
    dlg.adjustSize()
    top = win.geometry().top()
    bar = getattr(win, "toolbar", None)
    if bar is not None and bar.isVisible():
        top += bar.geometry().bottom()
    dlg.move(win.geometry().center().x() - dlg.width() // 2, top + 8)


def fade(dlg):
    if reduced_motion() or not blocking():
        return
    a = QPropertyAnimation(dlg, b"windowOpacity", dlg)
    a.setDuration(150)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.start()
    dlg._fade = a


def run(dlg: QDialog, kind="sheet") -> int:
    """exec() a sheet (window-modal); offscreen: recorded, 0 (rejected)."""
    if not blocking():
        shown.append((kind, dlg.windowTitle(), getattr(dlg, "body_text", "")))
        return 0
    place(dlg)
    fade(dlg)
    return dlg.exec()


class Sheet(QDialog):
    """Icon, headline, body, then buttons on the right with the default (named) last."""

    def __init__(self, parent, headline, body="", icon="info", role="accent", buttons=(), default=None,
                 title="", destructive=False):
        super().__init__(_window(parent))
        prepare(self, title)
        self.headline_text, self.body_text = headline, body
        self.clicked_text = None
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(16)
        self.icon = QLabel()
        self.icon.setPixmap(icons.icon(icon, role, 32).pixmap(32, 32))
        self.icon.setAlignment(Qt.AlignTop)
        self.icon.setAccessibleName({"triangle-alert": tr("Warning"),
                                     "circle-alert": tr("Attention")}.get(icon, tr("Note")))
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        self.col = QVBoxLayout()
        self.col.setSpacing(8)
        h = QLabel(headline)
        h.setProperty("role", "headline")
        h.setWordWrap(True)
        h.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.col.addWidget(h)
        if body:
            b = QLabel(body)
            b.setWordWrap(True)
            b.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.col.addWidget(b)
        self.extra = QVBoxLayout()
        self.col.addLayout(self.extra)
        self.row = QHBoxLayout()
        self.row.addStretch(1)
        self.col.addSpacing(8)
        self.col.addLayout(self.row)
        lay.addLayout(self.col, 1)
        self.buttons = {}
        self.destructive = destructive
        action = default or (buttons[-1] if buttons else None)
        for text in buttons:
            if destructive:                              # (Cancel is the default: Return never destroys)
                b = self.add_button(text, default=text == "Cancel", accept=text != "Cancel")
                if text == action:
                    b.setProperty("role", "destructive")
            else:
                self.add_button(text, default=text == action)
        self.setAccessibleName(headline)
        self.setAccessibleDescription(re.sub(r"\s*<[^>]+>\s*", " ", body or "").strip())

    def add_button(self, text, default=False, accept=True, on=None):
        """A button that closes the sheet (accepting or not), or runs on() and keeps it open."""
        b = QPushButton(text)
        b.setAccessibleName(text)
        b.setAutoDefault(False)
        if default:
            b.setDefault(True)
            b.setProperty("role", "primary")
        b.clicked.connect(on if on else (lambda _=False, t=text: self.choose(t, accept)))
        self.row.addWidget(b)
        self.buttons[text] = b
        return b

    def choose(self, text, accept=True):
        self.clicked_text = text
        (self.accept if accept else self.reject)()

    def showEvent(self, e):
        super().showEvent(e)
        b = self.default_button()
        if b is not None and not isinstance(self.focusWidget(), QLineEdit):
            b.setFocus()

    def default_button(self):
        return next((b for b in self.buttons.values() if b.isDefault()), None)


def button_row(dlg, action, on_action, cancel=None):
    """A dialog's own buttons laid out as every sheet's are, whatever the desktop's button order:
    Cancel, then the default (named for its action) on the right. Returns (row, default button)."""
    cancel = cancel or tr("Cancel")
    row = QHBoxLayout()
    row.addStretch(1)
    c = QPushButton(cancel)
    c.setAccessibleName(cancel)
    c.setAutoDefault(False)
    c.clicked.connect(dlg.reject)
    ok = QPushButton(action)
    ok.setAccessibleName(action.rstrip("…"))
    ok.setDefault(True)
    ok.setProperty("role", "primary")
    ok.clicked.connect(on_action)
    row.addWidget(c)
    row.addWidget(ok)
    return row, ok


def _ask(parent, kind, headline, body, icon, role, buttons, default):
    s = Sheet(parent, headline, body, icon, role, buttons, default)
    if not blocking():
        shown.append((kind, headline, body))
        s.deleteLater()
        return None
    run(s, kind)
    return s.clicked_text


def _split(text):
    """Old message-box text, "First line.\n\nThe rest", as headline and body."""
    head, _, rest = (text or "").strip().partition("\n")
    return head.strip(), rest.strip()


def warning(parent, text, body=None):
    head, rest = _split(text) if body is None else (text, body)
    _ask(parent, "warning", head, rest, "triangle-alert", "attention", (tr("Close"),), tr("Close"))


def information(parent, text, body=None):
    head, rest = _split(text) if body is None else (text, body)
    _ask(parent, "information", head, rest, "info", "accent", (tr("Close"),), tr("Close"))


def question(parent, headline, body="", action=None, cancel=None) -> bool:
    action, cancel = action or tr("Continue"), cancel or tr("Cancel")
    return _ask(parent, "question", headline, body, "circle-alert", "attention", (cancel, action), action) == action


def confirm_body(changes, undo) -> str:
    """What changes, then how to undo it; rich text (a list with hanging lines) stays rich."""
    if changes.lstrip().startswith("<"):
        return changes + (f"<p>{html.escape(undo)}</p>" if undo else "")
    return changes + ("\n\n" + undo if undo else "")


def confirm(parent, what, changes, undo, action, destructive=False) -> bool:
    """A destructive or library-wide action: names what changes and how to undo it. A
    destructive one (undo a job, remove a rule, stop watching) has Cancel as its default and its
    action in red: Return or Esc cancels, the action is clicked or reached with Tab."""
    body = confirm_body(changes, undo)
    s = Sheet(parent, what, body, "circle-alert", "attention", (tr("Cancel"), action), action, destructive=destructive)
    if not blocking():
        shown.append(("confirm", what, body))
        s.deleteLater()
        return False
    run(s, "confirm")
    return s.clicked_text == action


def ask(parent, headline, body="", buttons=None, default=None, offscreen=None):
    """A sheet with several named buttons (the default last unless named): the text of the one
    chosen, None for Esc. Offscreen: recorded, and `offscreen` returned."""
    buttons = buttons or (tr("Cancel"), tr("Continue"))
    s = Sheet(parent, headline, body, "circle-alert", "attention", tuple(buttons), default)
    if not blocking():
        shown.append(("ask", headline, body))
        s.deleteLater()
        return offscreen
    run(s, "ask")
    return s.clicked_text


def _file_dialog(parent, title, start):
    from PySide6.QtWidgets import QFileDialog
    dlg = QFileDialog(_window(parent), title, str(start or Path.home()))
    dlg.setWindowModality(Qt.WindowModal)              # (modal to its window only: Preferences stays usable)
    dlg.setWindowTitle(title)
    return dlg


def choose_folder(parent, title, start="") -> str:
    """A folder picker, window-modal: the folder chosen, or "" (offscreen: recorded, "")."""
    from PySide6.QtWidgets import QFileDialog
    if not start:                                      # (Preferences › General › Music folder)
        from .common import music_folder
        start = music_folder()
    dlg = _file_dialog(parent, title, start)
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    dlg.setLabelText(QFileDialog.Accept, tr("Choose"))
    if not blocking():
        shown.append(("choose_folder", title, ""))
        dlg.deleteLater()
        return ""
    ok = dlg.exec() == QDialog.Accepted and dlg.selectedFiles()
    out = dlg.selectedFiles()[0] if ok else ""
    dlg.deleteLater()
    return out


def save_file(parent, title, start="", filters="") -> str:
    """A save-as picker, window-modal: the path chosen, or "" (offscreen: recorded, "")."""
    from PySide6.QtWidgets import QFileDialog
    dlg = _file_dialog(parent, title, Path(start).parent if start else "")
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    if filters:
        dlg.setNameFilter(filters)
    if start:
        dlg.selectFile(Path(start).name)
    if not blocking():
        shown.append(("save_file", title, ""))
        dlg.deleteLater()
        return ""
    ok = dlg.exec() == QDialog.Accepted and dlg.selectedFiles()
    out = dlg.selectedFiles()[0] if ok else ""
    dlg.deleteLater()
    return out


def get_text(parent, headline, body="", text="", action=None, placeholder="") -> tuple[str, bool]:
    action = action or tr("Continue")
    s = Sheet(parent, headline, body, "pencil", "accent")
    e = QLineEdit(text)
    e.setAccessibleName(headline)
    e.setPlaceholderText(placeholder)
    s.extra.addWidget(e)
    s.add_button(tr("Cancel"), accept=False)
    s.add_button(action, default=True)
    if not blocking():
        shown.append(("get_text", headline, body))
        s.deleteLater()
        return "", False
    e.selectAll()
    e.setFocus()
    ok = run(s, "get_text") == QDialog.Accepted
    return e.text(), ok


def show_text(parent, headline, text, body="", mono=True, copy=False):
    """Read-only text (a plan, a script, results) in a sheet, monospace for scripts and logs."""
    s = Sheet(parent, headline, body, "info", "accent")
    s.setMaximumWidth(16777215)
    s.resize(760, 460)
    t = QPlainTextEdit(text)
    t.setReadOnly(True)
    t.setAccessibleName(headline)
    if mono:
        t.setFont(style.mono())
    s.extra.addWidget(t, 1)
    if copy:
        s.add_button(tr("Copy"), on=lambda: QApplication.clipboard().setText(text))
    s.add_button(tr("Close"), default=True)
    if not blocking():
        shown.append(("show_text", headline, body))
        s.deleteLater()
        return
    run(s, "show_text")
