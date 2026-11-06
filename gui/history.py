"""gui/history.py - The History page: what the rules excluded."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import sheets
from .common import ex, filter_table, folder_item, heading, left_header, status_cell
from .i18n import N_, tr

# Clear History…: (days kept back, words); None clears everything
CLEAR_CHOICES = ((None, N_("Everything")), (1, N_("Older Than 1 Day")), (5, N_("Older Than 5 Days")),
                 (10, N_("Older Than 10 Days")))


class History(QWidget):
    def __init__(self, win):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("History"), tr("What the rules excluded, and why. Job results are under Jobs.")))
        self.table = QTableWidget(0, 4)                     # (the job: in the When cell's tooltip)
        self.table.setHorizontalHeaderLabels([tr("When"), tr("Excluded as"), tr("Rule"), tr("Item")])
        h = self.table.horizontalHeader()
        for c in range(3):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setTextElideMode(Qt.ElideRight)
        left_header(self.table)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAccessibleName(tr("What the rules excluded"))
        from .widgets import EmptyOverlay
        self._empty = EmptyOverlay(self.table, "history", tr("Nothing excluded yet."),
                                   tr("Albums and files your Ignore and Skip rules leave alone are listed here."))
        lay.addWidget(self.table, 1)
        b = QPushButton(tr("Refresh"))
        b.setAccessibleName(tr("Refresh"))
        b.setToolTip(tr("Refresh (Ctrl+R)"))
        b.clicked.connect(self.reload)
        from PySide6.QtWidgets import QHBoxLayout, QMenu
        line = QHBoxLayout()
        line.addWidget(b)
        self.clear_btn = QPushButton(tr("Clear History…"))
        self.clear_btn.setAccessibleName(tr("Clear History"))
        self.clear_btn.setToolTip(tr("Forget what was excluded: all of it, or what is older than a few days"))
        m = QMenu(self.clear_btn)
        for days, words in CLEAR_CHOICES:
            m.addAction(tr(words)).triggered.connect(lambda _=False, d=days: self.clear(d))
        self.clear_btn.setMenu(m)
        line.addWidget(self.clear_btn)
        line.addStretch(1)
        lay.addLayout(line)
        self.reload()

    def clear(self, days) -> int:
        """Clear History… (asked first): everything, or what is older than DAYS days."""
        what = (tr("Clear the whole history?") if days is None else
                tr("Clear the history older than {n} days?").format(n=days) if days != 1 else
                tr("Clear the history older than 1 day?"))
        if not sheets.confirm(self, what, tr("The list of what the rules excluded is cleared. The rules, and "
                                             "what they leave alone, stay as they are."),
                              tr("This cannot be undone."), tr("Clear History"), destructive=True):
            return 0
        n = ex.State().clear_history(days)
        self.reload()
        return n

    def reload(self):
        rows = ex.State().history(500)
        self.table.setRowCount(len(rows))
        for i, (ts, job, item, label, rname, _d) in enumerate(rows):
            when = QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)))
            when.setToolTip(tr("Job {job}").format(job=job) if job else "")
            what = status_cell(QTableWidgetItem(), "excluded", label)
            for j, it in enumerate((when, what, QTableWidgetItem(rname), folder_item(item))):
                self.table.setItem(i, j, it)

    def filter(self, text):
        filter_table(self.table, text)
