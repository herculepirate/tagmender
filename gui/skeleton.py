"""gui/skeleton.py - a list's skeleton while its rows load: grey bars in the shape of the rows to come
(the view's own row height and column widths), so nothing jumps when they arrive.

  sk = Skeleton.over(view)          one per view (a QTableView, QListView or any QAbstractScrollArea)
  sk.start()                        bars over the empty view; a soft pulse, still with reduced motion
  sk.stop()                         the rows are there (or nothing was found: the view's own empty state)
  sk.fail("words", retry=fn)        no bars: what went wrong, in words, and Try Again

Slow: after SLOW seconds a line under the bars says it is still working; after LONG seconds it
says so plainly and offers Stop when the caller gave one (start(cancel=fn)) - a skeleton is never
the last thing you see. The pulse follows common.reduced_motion() (Preferences > General > Reduce
motion, else the desktop's own animation setting): then the bars stand still.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableView, QVBoxLayout,
                               QWidget)

from . import style
from .i18n import tr

SLOW, LONG = 6, 25                       # seconds: "still working", then "taking longer than usual"
PULSE_MS = 1600                          # one breath of the bars (light to dark and back)
BAR_H = 10                               # a bar's height: a line of text, not the row
WIDTHS = (0.82, 0.64, 0.74, 0.58, 0.9, 0.68, 0.77, 0.6)   # each row's bars a little shorter or longer


class Skeleton(QWidget):
    """The bars, drawn over a view's viewport while it is empty and loading."""

    @classmethod
    def over(cls, view) -> "Skeleton":
        sk = getattr(view, "_skeleton", None)
        if sk is None:
            sk = cls(view)
            view._skeleton = sk
        return sk

    def __init__(self, view):
        port = view.viewport() if hasattr(view, "viewport") else view
        super().__init__(port)
        self.view, self.port = view, port
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAccessibleName(tr("Loading"))
        self._phase, self._failed = 0.5, False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(PULSE_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self._slow, self._long = QTimer(self), QTimer(self)
        for t, fn in ((self._slow, self._said_slow), (self._long, self._said_long)):
            t.setSingleShot(True)
            t.timeout.connect(fn)
        # the words under the bars (slow), or in their place (failed), with one button
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.SPACE["m"], style.SPACE["m"], style.SPACE["m"], style.SPACE["m"])
        lay.addStretch(1)
        self.words = QLabel("")
        self.words.setWordWrap(True)
        self.words.setAlignment(Qt.AlignCenter)
        self.words.setProperty("role", "hint")
        self.words.hide()
        lay.addWidget(self.words)
        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton("")
        self.button.setAutoDefault(False)
        self.button.hide()
        self.button.clicked.connect(self._pressed)
        row.addWidget(self.button)
        row.addStretch(1)
        lay.addLayout(row)
        self._action = None
        self.hide()
        port.installEventFilter(self)

    # ---- the caller's three calls ------------------------------------------------------------
    def start(self, cancel=None):
        """Loading: the bars, pulsing (unless motion is reduced), the slow-load timers running."""
        from .common import reduced_motion
        self._failed, self._cancel = False, cancel
        self._say("", None)
        self._fit()
        self.show()
        self.raise_()
        if reduced_motion():
            self._anim.stop()
            self._phase = 0.5
        else:
            self._anim.start()
        self._slow.start(SLOW * 1000)
        self._long.start(LONG * 1000)
        self.update()

    def stop(self):
        """Loaded (rows, or none: the view's own empty words show): the bars go."""
        self._anim.stop()
        self._slow.stop()
        self._long.stop()
        self.hide()

    def fail(self, words, retry=None):
        """It did not work: no bars, the words (plain, what to do) and Try Again when there is RETRY."""
        self._anim.stop()
        self._slow.stop()
        self._long.stop()
        self._failed = True
        self._say(words, (tr("Try Again"), retry) if retry else None)
        self._fit()
        self.show()
        self.raise_()
        self.update()

    def loading(self) -> bool:
        return self.isVisible() and not self._failed

    # ---- inside ------------------------------------------------------------------------------
    def _said_slow(self):
        self._say(tr("Still working… the music databases can be slow at busy times."), None)

    def _said_long(self):
        self._say(tr("This is taking longer than usual. You can keep waiting, or stop and try again later."),
                  (tr("Stop"), self._cancel) if getattr(self, "_cancel", None) else None)

    def _say(self, words, action):
        self.words.setText(words)
        self.words.setVisible(bool(words))
        self._action = action[1] if action else None
        self.button.setText(action[0] if action else "")
        self.button.setVisible(bool(action))
        if words:
            self.setAccessibleDescription(words)

    def _pressed(self):
        fn = self._action
        if fn is not None:
            if self._failed:
                self.stop()
            fn()

    def _tick(self, v):
        self._phase = float(v)
        self.update()

    def _fit(self):
        self.setGeometry(self.port.rect())

    def eventFilter(self, obj, e):
        port = getattr(self, "port", None)                # (none while being built or taken down)
        if port is not None and obj is port and e.type() == e.Type.Resize:
            self._fit()
        return False

    def _row_height(self) -> int:
        v = self.view
        if isinstance(v, QTableView):
            return max(20, v.verticalHeader().defaultSectionSize())
        if isinstance(v, QAbstractItemView) and v.gridSize().isValid() and v.gridSize().height() > 0:
            return v.gridSize().height()
        return style.ROW + 8

    def _columns(self) -> list:
        """(left, width) of each column the bars stand in: the table's own, else one wide one."""
        v, w = self.view, self.width()
        if isinstance(v, QTableView):
            h = v.horizontalHeader()
            cols = [(h.sectionViewportPosition(c), h.sectionSize(c)) for c in range(h.count())
                    if not h.isSectionHidden(c) and h.sectionSize(c) > 24]
            if cols:
                return cols
        return [(style.SPACE["m"], w - 2 * style.SPACE["m"])]

    def paintEvent(self, _e):
        if self._failed:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        c = QColor(style.color("line"))
        # (a breath between a light and a slightly darker grey: never a blink)
        c.setAlphaF(0.55 + 0.4 * self._phase)
        p.setBrush(c)
        rh = self._row_height()
        bottom = self.height() - (self.words.height() + 40 if self.words.isVisible() else 0)
        n, y = 0, 0
        while y + rh <= max(rh, bottom):
            for k, (x, cw) in enumerate(self._columns()):
                frac = WIDTHS[(n + k * 3) % len(WIDTHS)]
                bw = max(16.0, (cw - 2 * style.SPACE["s"]) * frac)
                p.drawRoundedRect(QRectF(x + style.SPACE["s"], y + (rh - BAR_H) / 2, bw, BAR_H),
                                  BAR_H / 2, BAR_H / 2)
            n, y = n + 1, y + rh
            if n >= 12:                                   # (a dozen rows say "a list is coming" enough)
                break
        p.end()
