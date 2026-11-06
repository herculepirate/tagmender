"""gui/history.py - The History page: what the rules excluded."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .common import ex, filter_table, folder_item, heading, left_header, status_cell
from .i18n import tr


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
        lay.addWidget(b, 0, Qt.AlignLeft)
        self.reload()

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
