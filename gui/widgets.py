"""gui/widgets.py - Small widgets shared by pages: the drop area, the sidebar source list and
empty states."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel, QLayout, QListWidget,
                               QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout)

from . import icons, style
from .common import music_paths
from .i18n import N_, tr


class ElidedPath(QLabel):
    """A path on one line, shortened in the middle to fit (the whole path in its tooltip)."""
    def __init__(self, text="", empty=""):
        super().__init__()
        self.full = ""
        self.empty = empty                                  # (shown while there is no path)
        self.setMinimumWidth(80)
        self.setText(text)

    def setText(self, text):
        self.full = text or ""
        self.setToolTip(self.full)
        self.setAccessibleName(tr("Folder: {0}").format(self.full or self.empty))
        self._fit()

    def text(self):
        return self.full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        super().setText(self.fontMetrics().elidedText(self.full or self.empty, Qt.ElideMiddle, max(40, self.width())))

    def minimumSizeHint(self):
        return QSize(80, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(240, super().sizeHint().height())


class SleeveDrop(QFrame):
    """The empty state: drop a folder on an empty record sleeve, or click to choose one."""
    dropped = Signal(str)
    HEADLINE = N_("Drop a music folder here or press Ctrl+O")
    HINT = N_("An album, a whole library, or loose files. Nothing changes until you apply the preview.")

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(170)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr("Drop a music folder here, or press Enter to choose one (Ctrl+O)"))
        self.setCursor(Qt.PointingHandCursor)
        self.hover = False

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(style.color("accent"))
        line = accent if (self.hover or self.hasFocus()) else self.palette().color(QPalette.Mid)
        side = max(60, min(self.height() - 120, 190))
        x, y = (self.width() - side * 1.5) / 2, (self.height() - side) / 2 - 40
        r = side * 0.46
        cx, cy = x + side * (1.08 if self.hover else 0.96), y + side / 2   # the disc slides out on hover
        p.setPen(QPen(line, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))                # the disc, behind
        p.setBrush(self.palette().color(QPalette.Window))
        p.setPen(QPen(line, 2, Qt.DashLine if not self.hover else Qt.SolidLine))
        sleeve = QRectF(x, y, side, side)
        p.drawRoundedRect(sleeve, 4, 4)                                     # the sleeve, in front
        p.setBrush(Qt.NoBrush)
        if cx - r * 0.16 > x + side + 1:                                    # the hole: only once the disc is out
            p.setPen(QPen(line, 1.5))
            p.drawEllipse(QRectF(cx - r * 0.16, cy - r * 0.16, r * 0.32, r * 0.32))
        # the headline and the hint wrap at 960 px (measured, so the hint sits under the headline)
        width = self.width() - 32
        ty = y + side + 24                                                  # under the sleeve
        p.setPen(self.palette().color(QPalette.WindowText))
        head_font = style.font("headline")                                  # (below the page title in size)
        head_font.setPixelSize(style.TYPE["headline"][0])
        p.setFont(head_font)
        flags = Qt.AlignHCenter | Qt.TextWordWrap
        head = p.fontMetrics().boundingRect(QRect(0, 0, width, 1000), flags, tr(self.HEADLINE))
        p.drawText(QRectF(16, ty, width, head.height()), flags, tr(self.HEADLINE))
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.PlaceholderText))
        hint_w = max(40, self.width() - 64)
        hint = p.fontMetrics().boundingRect(QRect(0, 0, hint_w, 1000), flags, tr(self.HINT))
        p.drawText(QRectF(32, ty + head.height() + 8, hint_w, hint.height()), flags, tr(self.HINT))

    def dragEnterEvent(self, e):
        """Folders and audio files only, as anywhere in the window: an image, a PDF or a web
        link is refused (no hover, no drop)."""
        if music_paths(e.mimeData()):
            self.hover = True
            self.update()
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, _e):
        self.hover = False
        self.update()

    def dropEvent(self, e):
        self.hover = False
        self.update()
        paths = music_paths(e.mimeData())
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        win = self.window()
        if hasattr(win, "dropped"):                          # (the window says when only the first is taken)
            win.dropped(paths)
        else:
            self.dropped.emit(paths[0])

    def mousePressEvent(self, e):
        e.accept() if e.button() == Qt.LeftButton else e.ignore()

    def mouseReleaseEvent(self, e):
        """A left click (released inside) chooses a folder; a right or middle click does not."""
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.choose()
        else:
            super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.choose()
        else:
            super().keyPressEvent(e)

    def choose(self):
        from . import sheets
        d = sheets.choose_folder(self, tr("Choose a music folder to preview"))
        if d:
            self.dropped.emit(d)


# --------------------------------------------------------------------------
# the sidebar: a source list with section headers, icons and count badges
# --------------------------------------------------------------------------
HEADER, PAGE, BADGE, ICON = Qt.UserRole + 1, Qt.UserRole, Qt.UserRole + 2, Qt.UserRole + 3


class SourceDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, style.ROW + (8 if index.data(HEADER) else 0))

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        if index.data(HEADER):
            p.setFont(style.font("caption"))
            f = p.font()
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(QColor(style.color("muted")))
            p.drawText(r.adjusted(10, 8, -6, 0), Qt.AlignLeft | Qt.AlignVCenter, index.data(Qt.DisplayRole))
            p.restore()
            return
        sel = bool(option.state & QStyle.State_Selected)
        focused = bool(option.state & QStyle.State_HasFocus) or (option.widget and option.widget.hasFocus())
        box = QRectF(r).adjusted(0, 1, 0, -1)
        if sel:
            # (a soft tint of the accent, its text in ink, as a Mac sidebar; grey when the list
            # is not focused)
            p.setPen(Qt.NoPen)
            tint = QColor(style.color("accent"))
            tint.setAlphaF(0.30 if style.is_dark() else 0.16)
            p.setBrush(tint if focused else QColor(style.color("line")))
            p.drawRoundedRect(box, style.CONTROL, style.CONTROL)
        on_sel = False                                    # (ink on the tint: no white text)
        ink = style.color("ink")
        name = index.data(ICON)
        if name:
            icons.icon(name, "accent", 16).paint(
                p, QRect(r.left() + 10, r.center().y() - 8, 16, 16))

        n = index.data(BADGE) or 0
        right = r.right() - 8
        if n:
            p.setFont(style.font("caption"))
            text = str(n)
            w = max(20, p.fontMetrics().horizontalAdvance(text) + 12)
            pill = QRectF(right - w, r.center().y() - 9, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(style.color("on_selection" if on_sel else "line")))   # (a light pill on the selection)
            p.drawRoundedRect(pill, 9, 9)
            p.setPen(QColor(style.color("selection" if on_sel else "ink")))
            p.drawText(pill, Qt.AlignCenter, text)
            right = int(pill.left()) - 6
        p.setFont(option.font)
        if sel and focused:
            f = QFont(option.font)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
        p.setPen(QColor(ink))
        p.drawText(QRect(r.left() + 34, r.top(), right - r.left() - 34, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(index.data(Qt.DisplayRole), Qt.ElideRight, right - r.left() - 34))
        p.restore()


class SourceList(QListWidget):
    """Section headers are not selectable and the arrow keys pass over them."""

    def __init__(self):
        super().__init__()
        self.setObjectName("nav")
        self.setItemDelegate(SourceDelegate(self))
        self.setAccessibleName(tr("Sidebar"))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def add_header(self, text):
        it = QListWidgetItem(tr(text))
        it.setData(HEADER, True)
        it.setFlags(Qt.NoItemFlags)
        it.setData(Qt.AccessibleTextRole, tr("{0} section").format(tr(text)))
        self.addItem(it)
        return it

    def add_page(self, name, icon):
        it = QListWidgetItem(tr(name))
        it.setData(PAGE, name)
        it.setData(ICON, icon)
        it.setData(Qt.AccessibleTextRole, tr(name))
        self.addItem(it)
        return it

    def row_of(self, name):
        return next(i for i in range(self.count()) if self.item(i).data(PAGE) == name)

    def set_badge(self, name, n, noun=None):
        it = self.item(self.row_of(name))
        it.setData(BADGE, n or 0)
        it.setData(Qt.AccessibleTextRole, f"{tr(name)}, {n} {noun or tr('albums')}" if n else tr(name))
        self.viewport().update()

    def badge(self, name):
        return self.item(self.row_of(name)).data(BADGE) or 0

    def moveCursor(self, action, modifiers):
        idx = super().moveCursor(action, modifiers)
        step = -1 if action in (QAbstractItemView.MoveUp, QAbstractItemView.MovePageUp,
                                QAbstractItemView.MoveHome, QAbstractItemView.MovePrevious) else 1
        row = idx.row()
        while 0 <= row < self.count() and self.item(row).data(HEADER):
            row += step
        if not 0 <= row < self.count():                      # (a header at either end)
            row = self.currentRow()
        return self.model().index(row, 0)


class EmptyState(QFrame):
    """What to do next, centred: an icon, a headline and a hint."""

    def __init__(self, icon, headline, hint=""):
        super().__init__()
        self.icon_name = icon
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.pic)
        self.head = QLabel(headline)
        self.head.setProperty("role", "headline")
        self.head.setAlignment(Qt.AlignHCenter)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.hint = QLabel(hint)
        self.hint.setProperty("role", "hint")
        self.hint.setAlignment(Qt.AlignHCenter)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        lay.addStretch(2)
        self.setAccessibleName(headline)
        self.retint()

    def set_text(self, headline, hint=""):
        self.head.setText(headline)
        self.hint.setText(hint)
        self.setAccessibleName(headline)

    def retint(self):
        self.pic.setPixmap(icons.icon(self.icon_name, "faint" if not style.is_dark() else "muted", 40).pixmap(40, 40))


class EmptyOverlay(QObject):
    """An empty list says so, centred in it (an icon, a headline and a hint), as a Mac list
    does; it goes as soon as the list has a row."""

    def __init__(self, view, icon, headline, hint=""):
        super().__init__(view)
        self.view = view
        self.state = EmptyState(icon, headline, hint)
        self.state.setParent(view.viewport())
        self.state.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.state.setStyleSheet("background: transparent;")
        view.viewport().installEventFilter(self)
        m = view.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset, m.layoutChanged):
            sig.connect(self.update)
        self.update()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize:
            try:
                self.fit()
            except RuntimeError:                              # (the list is being closed)
                pass
        return False

    def fit(self):
        """A short list: the words alone (no icon), or the headline alone."""
        r = self.view.viewport().rect()
        self.state.setGeometry(r)
        self.state.pic.setVisible(r.height() >= 150)
        self.state.hint.setVisible(r.height() >= 56)
        self.state.layout().setContentsMargins(8, 2 if r.height() < 150 else 8, 8, 2 if r.height() < 150 else 8)

    def update(self, *_):
        try:
            empty = self.view.model().rowCount() == 0
            self.fit()
            self.state.setVisible(empty)
        except RuntimeError:                                  # (the list was closed: nothing to show)
            pass


class FlowLayout(QLayout):
    """A row of buttons that wraps onto the next line when the window is narrow (960 px), so no
    button is ever squeezed below its text."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def addStretch(self, *_):                              # (a row of buttons reads left to right anyway)
        pass

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):
        w = sum(it.sizeHint().width() for it in self.items if not it.isEmpty()) \
            + self.spacing() * max(0, len(self.items) - 1)
        h = max((it.sizeHint().height() for it in self.items), default=0)
        return QSize(w, h)

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize().expandedTo(it.sizeHint().boundedTo(it.maximumSize())))
        return s

    def _place(self, rect, move):
        x, y, line = rect.x(), rect.y(), 0
        for it in self.items:
            if it.isEmpty():
                continue
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + min(self.spacing(), 6), 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y()
