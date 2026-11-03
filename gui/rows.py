"""gui/rows.py - Grouped inset lists for settings, shared by the Profile editor and Preferences.

A settings page reads like a list of cards: each Group is an inset card of rows, a row has its
label on the left and its control on the right, a short footnote under the label, and the
group's own footnote under the card. Explanations are never only in a tooltip.

  Switch       an on/off toggle (a QCheckBox drawn as a switch: isChecked, toggled ...)
  Segmented    two or three short, equal choices (the QComboBox calls the editor uses)
  Popup        a pop-up menu (a QComboBox)
  Stepper      a small whole number
  (Popup and Stepper take the scroll wheel only once clicked: scrolling a long page over
  them never changes a setting)
  FolderField  a folder: its path and Choose…; a missing folder says "Not available now"
  ListField    a list of names, edited in a sheet (one per line, "−" to remove, drag to order)
  Row          label, change marker, footnote, problem line, reason line, control
  Group        an inset card of rows with a title and a footnote
  Disclosure   the "Advanced" row that shows or hides what follows it
"""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons, sheets, style
from .i18n import tr

ROW_HEIGHT = 44


def caption(text="", role="caption"):
    lb = QLabel(text)
    lb.setProperty("role", role)
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return lb


class Switch(QCheckBox):
    """An on/off switch: a pill with a knob, the accent colour when on. Its text is not drawn
    (the row's label says it) but is kept as its accessible name."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setAccessibleName(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(40, 24)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 2, 38, 20)
        on, enabled = self.isChecked(), self.isEnabled()
        track = style.color("accent") if on else style.color("muted")    # (3:1 or more on the card)
        if not enabled:
            track = style.color("line")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(track))
        p.drawRoundedRect(r, 10, 10)
        knob = QRectF(r.right() - 18 if on else r.left() + 2, r.top() + 2, 16, 16)
        p.setBrush(QColor("#FFFFFF" if enabled else style.color("window")))
        p.drawEllipse(knob)
        if self.hasFocus():
            p.setPen(QPen(QColor(style.color("accent")), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)


class Segmented(QWidget):
    """A segmented control: one choice of two or three, as buttons side by side. It answers the
    QComboBox calls the settings code uses (currentData, findData, setCurrentIndex ...)."""
    currentIndexChanged = Signal(int)

    def __init__(self, name=""):
        super().__init__()
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.items = []                                       # [(text, data, button)]
        self.group.idClicked.connect(self._clicked)
        self.setAccessibleName(name)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def addItem(self, text, data=None):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setProperty("role", "segment")
        b.setAccessibleName(text)
        b.setAutoDefault(False)
        self.group.addButton(b, len(self.items))
        self.lay.addWidget(b)
        self.items.append((text, data, b))
        if len(self.items) == 1:
            b.setChecked(True)
        self._ends()

    def _ends(self):
        for i, (_t, _d, b) in enumerate(self.items):
            b.setProperty("pos", "only" if len(self.items) == 1 else "first" if i == 0 else
                          "last" if i == len(self.items) - 1 else "middle")

    def _clicked(self, i):
        self.currentIndexChanged.emit(i)

    def count(self):
        return len(self.items)

    def currentIndex(self):
        return max(0, self.group.checkedId())

    def setCurrentIndex(self, i):
        if 0 <= i < len(self.items) and i != self.currentIndex():
            self.items[i][2].setChecked(True)
            self.currentIndexChanged.emit(i)

    def currentData(self):
        return self.items[self.currentIndex()][1] if self.items else None

    def currentText(self):
        return self.items[self.currentIndex()][0] if self.items else ""

    def itemText(self, i):
        return self.items[i][0]

    def itemData(self, i):
        return self.items[i][1]

    def findData(self, data):
        return next((i for i, (_t, d, _b) in enumerate(self.items) if d == data and type(d) is type(data)), -1)

    def setEnabled(self, on):
        super().setEnabled(on)
        for _t, _d, b in self.items:
            b.setEnabled(on)


def _wheel_when_focused(widget, base, event):
    """The wheel changes the control only when it has the focus; otherwise the page scrolls."""
    if widget.hasFocus():
        base.wheelEvent(widget, event)
    else:
        event.ignore()


class Popup(QComboBox):
    """A pop-up menu that ignores the scroll wheel until it is clicked."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        _wheel_when_focused(self, QComboBox, event)


class Stepper(QSpinBox):
    """A small whole number with its unit (At most 5 genres, up to 6 tries)."""

    def __init__(self, low, high, suffix=""):
        super().__init__()
        self.setRange(low, high)
        if suffix:
            self.setSuffix(suffix)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumWidth(88)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        _wheel_when_focused(self, QSpinBox, event)


class FolderField(QWidget):
    """A folder: the path (it may be typed) and Choose…; a folder that is not there now is
    said in orange under it (a share that is not mounted)."""
    textChanged = Signal(str)

    def __init__(self, name, title):
        super().__init__()
        self.title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setAccessibleName(name)
        self.edit.setMinimumWidth(120)
        self.edit.setPlaceholderText(tr("Not chosen"))
        self.edit.textChanged.connect(self._changed)
        row.addWidget(self.edit, 1)
        self.button = QPushButton(tr("Choose…"))
        self.button.setAccessibleName(tr("Choose: {0}").format(name))
        self.button.setAutoDefault(False)
        self.button.clicked.connect(self.choose)
        row.addWidget(self.button)
        v.addLayout(row)
        self.missing = caption(tr("Not available now"), "warn")
        self.missing.hide()
        v.addWidget(self.missing)

    def _changed(self, text):
        path = os.path.expanduser(text.strip())
        self.missing.setVisible(bool(text.strip()) and os.path.isabs(path) and not os.path.isdir(path))
        self.textChanged.emit(text)

    def choose(self):
        d = sheets.choose_folder(self, self.title, self.edit.text() or os.path.expanduser("~/Music"))
        if d:
            self.edit.setText(d)

    def text(self):
        return self.edit.text()

    def setText(self, text):
        self.edit.setText(text)
        self.edit.setCursorPosition(0)

    def setEnabled(self, on):
        super().setEnabled(on)
        self.edit.setEnabled(on)
        self.button.setEnabled(on)


class ListSheet(QDialog):
    """Edit a list of names: one row each, "+" adds, "−" removes, rows are dragged to reorder
    (checkable: each row has a switch-like tick, for an ordered list of sources)."""

    def __init__(self, parent, headline, body, items, checkable=False, known=None, words=None, folders=False):
        super().__init__(parent.window() if parent is not None else None)
        sheets.prepare(self, headline)
        self.resize(460, 420)
        self.checkable, self.words, self.folders = checkable, words or {}, folders
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        head = QLabel(headline)
        head.setProperty("role", "headline")
        lay.addWidget(head)
        if body:
            lay.addWidget(caption(body, "hint"))
        self.list = QListWidget()
        self.list.setAccessibleName(headline)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        lay.addWidget(self.list, 1)
        if checkable:
            on = list(items)
            for k in on + [k for k in (known or []) if k not in on]:
                it = QListWidgetItem(self.words.get(k, k))
                it.setData(Qt.UserRole, k)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                it.setCheckState(Qt.Checked if k in on else Qt.Unchecked)
                self.list.addItem(it)
        else:
            for k in items:
                self.add(str(k))
        row = QHBoxLayout()
        if not checkable:
            self.entry = QLineEdit()
            self.entry.setAccessibleName(tr("New item"))
            self.entry.setPlaceholderText(tr("Type a folder, or Choose Folder…") if folders else
                                          tr("Type a name, then Add"))
            self.entry.returnPressed.connect(self.add_typed)
            row.addWidget(self.entry, 1)
            if folders:                                  # (a folder is picked, not typed)
                pick = QPushButton(tr("Choose Folder…"))
                pick.setAutoDefault(False)
                pick.setAccessibleName(tr("Choose a folder to add"))
                pick.clicked.connect(self.add_chosen)
                row.addWidget(pick)
            add = QToolButton()
            add.setProperty("role", "tool")
            add.setIcon(icons.icon("plus", "ink"))
            add.setAccessibleName(tr("Add"))
            add.setToolTip(tr("Add"))
            add.clicked.connect(self.add_typed)
            row.addWidget(add)
            rm = QToolButton()
            rm.setProperty("role", "tool")
            rm.setIcon(icons.icon("minus", "ink"))
            rm.setAccessibleName(tr("Remove the selected item"))
            rm.setToolTip(tr("Remove the selected item"))
            rm.clicked.connect(self.remove)
            row.addWidget(rm)
        else:
            row.addWidget(caption(tr("Drag to change the order. Only ticked ones are used."), "hint"), 1)
        lay.addLayout(row)
        buttons, _ok = sheets.button_row(self, tr("Done"), self.accept)
        lay.addLayout(buttons)

    def add(self, text):
        it = QListWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
        self.list.addItem(it)

    def add_typed(self):
        text = self.entry.text().strip()
        if text:
            self.add(text)
            self.entry.clear()

    def add_chosen(self):
        from .common import music_folder
        last = self.list.item(self.list.count() - 1).text() if self.list.count() else ""
        start = os.path.dirname(os.path.expanduser(last)) if last else music_folder()
        folder = sheets.choose_folder(self, tr("Choose a folder to add"), start)
        if folder and folder not in self.items():
            self.add(folder)

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def items(self) -> list:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if self.checkable:
                if it.checkState() == Qt.Checked:
                    out.append(it.data(Qt.UserRole))
            elif it.text().strip():
                out.append(it.text().strip())
        return out


class ListField(QWidget):
    """A list shown as a short summary ("7 folders: .Trash*, @eaDir, …") with Edit…, which
    opens a ListSheet. checkable: an ordered choice of `known` names (sources)."""
    changed = Signal()

    def __init__(self, name, body="", checkable=False, known=None, words=None, noun=None, folders=False):
        super().__init__()
        self.name, self.body, self.checkable, self.folders = name, body, checkable, folders
        self.known, self.words, self.noun = known or [], words or {}, noun
        self._items = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        self.summary.setMinimumWidth(60)
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.summary, 1)
        self.button = QPushButton(tr("Edit…"))
        self.button.setAccessibleName(tr("Edit: {0}").format(name))
        self.button.setAutoDefault(False)
        self.button.clicked.connect(self.edit)
        row.addWidget(self.button)
        self.setAccessibleName(name)

    def value(self) -> list:
        return list(self._items)

    def set_value(self, items):
        self._items = list(items or [])
        shown = [self.words.get(x, str(x)) for x in self._items]
        self._text = text = ", ".join(shown) if shown else tr("None")
        self._elide()
        self.summary.setToolTip(", ".join(shown))
        self.summary.setAccessibleName(tr("{0}: {1}").format(self.name, text))

    def _elide(self):
        """The summary cut with "…" at its end to the width it has now (never clipped at the start)."""
        width = max(self.summary.width(), 60)
        self.summary.setText(self.summary.fontMetrics().elidedText(getattr(self, "_text", ""), Qt.ElideRight, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def edit(self):
        dlg = ListSheet(self, self.name, self.body, self._items, self.checkable, self.known, self.words,
                        folders=self.folders)
        if sheets.run(dlg, "list"):
            new = dlg.items()
            if new != self._items:
                self.set_value(new)
                self.changed.emit()

    def setEnabled(self, on):
        super().setEnabled(on)
        self.button.setEnabled(on)


class Row(QFrame):
    """One setting: its label (with a dot when changed from the default) and footnote on the
    left, the control on the right; under them a problem (red) or why it is greyed out."""

    def __init__(self, label, control, footnote=""):
        super().__init__()
        self.setObjectName("settingRow")
        self.label_text, self.footnote_text = label, footnote
        self.control = control
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.label = QLabel(label)
        # (only a long name wraps: a wrapping label is squeezed to its minimum width, measured
        # before the stylesheet's font applies, so short names broke onto two lines)
        if self.label.fontMetrics().horizontalAdvance(label) > 240:
            self.label.setWordWrap(True)
            self.label.setMinimumWidth(280)
        self.label.setBuddy(control)
        head.addWidget(self.label)
        self.dot = QLabel("●")
        self.dot.setProperty("role", "changed")
        self.dot.setAccessibleName(tr("changed from default"))
        self.dot.setToolTip(tr("Changed from the default"))
        self.dot.hide()
        head.addWidget(self.dot, 0, Qt.AlignVCenter)
        head.addStretch(1)
        left.addLayout(head)
        self.foot = caption(footnote)
        self.foot.setVisible(bool(footnote))
        left.addWidget(self.foot)
        line.addLayout(left, 1)
        wide = isinstance(control, (FolderField, ListField, QLineEdit))
        line.addWidget(control, 2 if wide else 0, Qt.AlignVCenter | (Qt.Alignment() if wide else Qt.AlignRight))
        outer.addLayout(line)
        self.reason = caption("", "caption")
        self.reason.hide()
        outer.addWidget(self.reason)
        self.problem = caption("", "error")
        self.problem.hide()
        outer.addWidget(self.problem)
        self.setMinimumHeight(ROW_HEIGHT)
        if not control.accessibleName():
            control.setAccessibleName(label)
        if footnote and not control.accessibleDescription():
            control.setAccessibleDescription(footnote)

    def set_changed(self, on):
        self.dot.setVisible(bool(on))

    def changed(self) -> bool:
        return not self.dot.isHidden()

    def set_problem(self, text):
        self.problem.setText(text)
        self.problem.setVisible(bool(text))
        self.problem.setAccessibleName(tr("Problem: {0}").format(text) if text else "")

    def set_reason(self, text):
        """Why the control is greyed out ("" when it is not)."""
        self.reason.setText(text)
        self.reason.setVisible(bool(text))
        self.control.setEnabled(not text)

    def words(self) -> str:
        """What the search field matches: the label, the footnote and the reason."""
        return " ".join((self.label_text, self.footnote_text, self.reason.text())).casefold()


class Group(QWidget):
    """An inset card of rows: a small title above it, hairlines between rows, a footnote under."""

    def __init__(self, title="", footnote=""):
        super().__init__()
        self.title_text = title
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.title = QLabel(title.upper() if title else "")
        self.title.setProperty("role", "grouptitle")
        self.title.setAccessibleName(title)
        self.title.setVisible(bool(title))
        self.title.setContentsMargins(16, 0, 0, 0)
        v.addWidget(self.title)
        self.card = QFrame()
        self.card.setProperty("role", "group")
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        v.addWidget(self.card)
        self.foot = caption(footnote)
        self.foot.setContentsMargins(16, 0, 16, 0)
        self.foot.setVisible(bool(footnote))
        v.addWidget(self.foot)
        self.rows = []

    def add(self, row):
        if self.rows:
            line = QFrame()
            line.setProperty("role", "separator")
            line.setFixedHeight(1)
            line.setContentsMargins(16, 0, 0, 0)
            self.body.addWidget(line)
            row.hairline = line
        else:
            row.hairline = None
        self.body.addWidget(row)
        self.rows.append(row)
        return row

    def add_widget(self, w):
        """A plain widget in the card (a preview line, a button row)."""
        box = QFrame()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.addWidget(w, 1)
        if self.rows:
            line = QFrame()
            line.setProperty("role", "separator")
            line.setFixedHeight(1)
            self.body.addWidget(line)
            box.hairline = line
        else:
            box.hairline = None
        self.body.addWidget(box)
        self.rows.append(box)
        return box

    def tidy(self):
        """Hairlines only between rows shown; the whole group hidden when none is."""
        first = True
        for r in self.rows:
            shown = not r.isHidden()
            if getattr(r, "hairline", None) is not None:
                r.hairline.setVisible(shown and not first)
            if shown:
                first = False
        self.setVisible(not first)


class SectionFold(QToolButton):
    """A section's heading that folds it: a chevron and the title; what it holds shows below
    it while it is open (open to start)."""

    toggled_by_user = Signal(bool)

    def __init__(self, text, holder):
        super().__init__()
        self.holder = holder
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(True)
        self.setProperty("role", "sectionfold")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setAccessibleName(text)
        self.setToolTip(tr("Click to fold or unfold this section"))
        self.toggled.connect(self._show)
        self.clicked.connect(lambda on: self.toggled_by_user.emit(on))
        self._show(True)

    def _show(self, on):
        self.setIcon(icons.icon("chevron-down" if on else "chevron-right", "ink", 18))
        self.setIconSize(QSize(18, 18))
        self.holder.setVisible(on)


class Disclosure(QToolButton):
    """The "Advanced" row: a chevron and its words; what it holds opens below it."""

    def __init__(self, text, holder):
        super().__init__()
        self.holder = holder
        self.setText(text)
        self.setCheckable(True)
        self.setProperty("role", "disclosure")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setAccessibleName(text)
        self.toggled.connect(self._show)
        self._show(False)

    def _show(self, on):
        self.setIcon(icons.icon("chevron-down" if on else "chevron-right", "muted", 14))
        self.holder.setVisible(on)
