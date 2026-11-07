"""gui/rubber.py - a rectangle dragged across a list selects the rows it covers, as in a file
manager's folder view. On every table that can select several rows (gui.common.left_header
installs it); a list that selects one row at a time is left as it is.

  press and drag (from a row or the empty space below the rows): the rectangle, and its rows
  selected as it grows; Ctrl (Cmd): added to the rows already selected; past the top or the
  bottom edge: the list scrolls. A short press (less than the drag distance) is an ordinary click.
  In a list whose rows can be dragged elsewhere (Manual Match's files), a drag from a row moves
  it, as in a file manager; the rectangle starts in the empty space.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QObject, QPoint, QRect, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QRubberBand, QTableView

MULTI = (QAbstractItemView.ExtendedSelection, QAbstractItemView.MultiSelection)


class RubberBand(QObject):
    @classmethod
    def on(cls, view) -> "RubberBand":
        rb = getattr(view, "_rubber", None)
        if rb is None:
            rb = cls(view)
            view._rubber = rb
        return rb

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.band = None
        self.origin = None                 # (where the press was, in the viewport)
        self.anchor = -1                   # (the row pressed on; past the last row: rowCount)
        self.active = False
        self.base = QItemSelection()
        view.viewport().installEventFilter(self)

    # ---- rows ----------------------------------------------------------------------------------
    def _rows(self) -> int:
        m = self.view.model()
        return m.rowCount() if m is not None else 0

    def _row_at(self, y) -> int:
        """The row at viewport height y; past the last row: rowCount (none)."""
        r = self.view.rowAt(y) if isinstance(self.view, QTableView) else self.view.indexAt(QPoint(4, y)).row()
        if r < 0:
            return self._rows() if y >= 0 else 0
        return r

    def _row_top(self, r) -> int:
        v = self.view
        if r >= self._rows():
            n = self._rows()
            if not n:
                return 0
            last = v.rowViewportPosition(n - 1) + v.rowHeight(n - 1) if isinstance(v, QTableView) else \
                v.visualRect(v.model().index(n - 1, 0)).bottom()
            return last
        return v.rowViewportPosition(r) if isinstance(v, QTableView) else v.visualRect(v.model().index(r, 0)).top()

    # ---- the events ----------------------------------------------------------------------------
    def eventFilter(self, obj, e):
        v = getattr(self, "view", None)
        try:
            if v is None or obj is not v.viewport() or v.selectionMode() not in MULTI:
                return False
        except RuntimeError:                                 # (the list taken down before its viewport)
            return False
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            self.origin = e.position().toPoint()
            if v.dragEnabled() and v.indexAt(self.origin).isValid():
                self.origin = None                           # (a list whose rows are dragged: as a folder,
                return False                                 # the rectangle starts in its empty space)
            self.anchor = self._row_at(self.origin.y())
            self.active = False
            add = bool(e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
            sm = v.selectionModel()
            self.base = QItemSelection(sm.selection()) if add and sm is not None else QItemSelection()
            return False                                     # (the click itself: Qt's, as ever)
        if t == QEvent.MouseMove and self.origin is not None and e.buttons() & Qt.LeftButton:
            pos = e.position().toPoint()
            if not self.active:
                if (pos - self.origin).manhattanLength() < QApplication.startDragDistance():
                    return False
                self.active = True
                if self.band is None:
                    self.band = QRubberBand(QRubberBand.Rectangle, v.viewport())
            self._drag(pos)
            return True                                      # (Qt's own drag-select would fight it)
        if t == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton and self.origin is not None:
            was = self.active
            self.origin, self.active = None, False
            if self.band is not None:
                self.band.hide()
            return was                                       # (a drag is not a click: no tick toggled)
        return False

    def _drag(self, pos):
        v = self.view
        port = v.viewport()
        h = port.height()
        bar = v.verticalScrollBar()
        if pos.y() < 0 and bar.value() > bar.minimum():      # (past an edge: the list scrolls)
            bar.setValue(bar.value() - max(1, bar.singleStep()))
        elif pos.y() > h and bar.value() < bar.maximum():
            bar.setValue(bar.value() + max(1, bar.singleStep()))
        y = min(max(pos.y(), 0), h - 1)
        cur = self._row_at(y)
        n = self._rows()
        # the rectangle: from the pressed row's place (it may have scrolled) to the mouse
        top = self._row_top(self.anchor) if self.anchor < n else self.origin.y()
        if self.anchor < n:
            top += min(max(self.origin.y() - self._row_top(self.anchor), 0), 1 << 16)
        top = min(max(top, 0), h)
        x0 = min(max(self.origin.x(), 0), port.width())
        x1 = min(max(pos.x(), 0), port.width())
        self.band.setGeometry(QRect(QPoint(x0, top), QPoint(x1, y)).normalized())
        self.band.show()
        lo, hi = min(self.anchor, cur), max(self.anchor, cur)
        hi = min(hi, n - 1)
        sm = v.selectionModel()
        if sm is None:
            return
        sel = QItemSelection(self.base)
        m = v.model()
        if n and lo <= hi:
            cols = max(1, m.columnCount())
            sel.merge(QItemSelection(m.index(lo, 0), m.index(hi, cols - 1)), QItemSelectionModel.Select)
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        if n and 0 <= cur < n:
            sm.setCurrentIndex(m.index(cur, 0), QItemSelectionModel.NoUpdate)
