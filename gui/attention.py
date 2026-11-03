"""gui/attention.py - The Needs attention page: albums marked Unresolved, by reason."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import icons, sheets
from .actions import plain
from .common import (filter_table, folder_item, heading, left_header, open_folder, plural, popup_menu, reasons,
                     search_web, status_cell)
from .i18n import N_, tr
from .widgets import ElidedPath, EmptyState, FlowLayout
from .work import start


# a reason code in a few words for the table (reasons.describe's sentence is in the tooltip)
SHORT = {"no_candidate": N_("Not found"), "incomplete_album": N_("Track count differs"),
         "ambiguous_release": N_("Two pressings fit"), "ambiguous_artist": N_("Artist unclear"),
         "conflicting_sources": N_("Sources disagree"), "web_fallback_only": N_("Only on the web"),
         "musicbrainz_unavailable": N_("MusicBrainz unreachable"), "discogs_unavailable": N_("Discogs unreachable"),
         "rate_limited": N_("Slowed down"), "missing_credentials": N_("Key missing"),
         "unsupported_format": N_("Format not writable"), "duration_mismatch": N_("Lengths differ"),
         "low_similarity": N_("Names differ"), "missing_collaborator": N_("Credit incomplete"),
         "write_failed": N_("Not written"),
         "partially_tagged": N_("Partly tagged"), "other": N_("Not identified")}


def short_reason(code) -> str:
    """A reason code in a few words; one this table has no words for in reasons' words (a code
    itself is never the label: it is in the tooltip and the inspector's Codes)."""
    if code in SHORT:
        return tr(SHORT[code])
    d = reasons.describe(code)
    return d[:1].upper() + d[1:] if d and d != code else tr(SHORT["other"])


class Attention(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows = []
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("Needs attention"),
                              tr("Albums marked Unresolved, with the reason. Retry looks them up "
                                                 "again; anything excluded by a rule is not listed here.")))
        row = QHBoxLayout()                                 # the folder, as Overview shows its own
        self.folder_icon = QLabel()
        self.folder_icon.setPixmap(icons.icon("folder-open", "muted").pixmap(16, 16))
        self.root = ElidedPath("", empty=tr("No folder chosen: choose the folder to look in"))
        self.choose_btn = ch = QPushButton(tr("Choose…"))
        ch.setAccessibleName(tr("Choose a folder to look in"))
        ch.clicked.connect(lambda: (lambda d: d and (self.root.setText(d), self.scan()))(
            sheets.choose_folder(self, tr("Choose the folder to look in"), self.root.text())))
        self.find_btn = sc = QPushButton(tr("Find Albums"))
        sc.setAccessibleName(tr("Find albums that need attention"))
        sc.setToolTip(tr("Find albums that need attention (Ctrl+R)"))
        sc.clicked.connect(self.scan)
        row.addWidget(self.folder_icon)
        row.addWidget(self.root, 1)
        row.addWidget(ch)
        row.addWidget(sc)
        lay.addLayout(row)
        self.views = QStackedWidget()
        self.empty = EmptyState("circle-check", tr("Nothing needs attention here."),
                                tr("Choose a folder and press Find Albums: albums an earlier run marked Unresolved are "
                                   "listed with the reason."))
        self.views.addWidget(self.empty)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Reason"), tr("Album folder"), tr("What was found")])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Interactive)      # (fitted: fit_reason)
        h.setSectionResizeMode(1, QHeaderView.Stretch)      # (the folder and what was found share the
        h.setSectionResizeMode(2, QHeaderView.Stretch)      #  rest: neither squeezes the other away at 960)
        self.table.setTextElideMode(Qt.ElideRight)          # (prose reads from its start)
        left_header(self.table)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.setAccessibleName(tr("Albums that need attention"))
        self.table.itemSelectionChanged.connect(self.selected_changed)
        self.table.activated.connect(lambda *_: self.retry())       # (Return, or a double click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: popup_menu(self, self.table, pos, [self.win.act(k) for k in ("retry", "search_web",
                                                                                     "open_folder", "copy_path")]))
        self.views.addWidget(self.table)
        lay.addWidget(self.views, 1)
        acts = FlowLayout()                     # (wraps when narrow; the commands' own words: one name each)
        self.buttons = []
        for key, fn in (("retry", self.retry), ("search_web", self.web), ("open_folder", self.open)):
            a = self.win.act(key) if self.win is not None else None
            text = plain(a.text()) if a is not None else key.replace("_", " ").capitalize()
            b = QPushButton(text)
            b.setAccessibleName(text)
            b.setToolTip(f"{text} ({a.shortcut().toString()})" if a is not None and a.shortcut().toString() else text)
            b.clicked.connect(fn)
            b.setEnabled(False)                              # (until an album is selected)
            acts.addWidget(b)
            self.buttons.append(b)
        self.report_btn = QPushButton(tr("Show Report…"))
        self.report_btn.setAccessibleName(tr("Show Report"))
        self.report_btn.setToolTip(tr("Every album listed, with what to do about it and the releases suggested"))
        self.report_btn.clicked.connect(self.show_report)
        self.report_btn.setEnabled(False)                   # (until albums are listed)
        acts.addWidget(self.report_btn)
        acts.addStretch(1)
        lay.addLayout(acts)
        self.scanning = False
        self.quiet = False                                  # (a look for the badge: no busy bar, no message)
        self.looks = 0                                      # (a newer look or list drops an older answer)

    def filter(self, text):
        filter_table(self.table, text)

    REASON_SHARE = 0.45                                 # the Reason column's most, of the table's width

    def fit_reason(self):
        """The reason as wide as its words, but never so wide the album's name is squeezed out
        (at 960 px with the inspector open; cut words are whole in the tooltip)."""
        t = self.table
        want = max(t.sizeHintForColumn(0), t.horizontalHeader().sectionSizeHint(0))
        m = self.layout().contentsMargins()                 # (the page's width: the table's own may be stale)
        most = int((self.width() - m.left() - m.right()) * self.REASON_SHARE)
        t.horizontalHeader().resizeSection(0, max(48, min(want, most)) if most > 0 else want)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit_reason()

    def scan(self, quiet=False):
        path = self.root.text().strip()
        if not path:
            if not quiet and self.choose_btn.isEnabled():
                self.choose_btn.click()
            return
        if self.scanning:                                    # (one look at a time)
            return

        def work(_w):
            import tagmender
            return tagmender._unresolved_albums(path)
        self.quiet = quiet
        self.searching(True)
        self.empty.set_text(tr("Looking…"), path)
        self.looks += 1
        look = self.looks
        start(self, work, lambda rows: self.found(look, rows), failed=self.scan_failed)

    def found(self, look, rows):
        if look != self.looks:                               # (the list was filled since: that stands)
            self.searching(False)
            return
        self.show_rows(rows)

    def count_under(self, path):
        """The badge after a preview or Apply: the albums marked Unresolved under the folder this
        page looks in - the folder previewed when none was chosen here. One rule for the badge on
        every page, so it never changes just because the page was opened."""
        root = self.root.text().strip()
        if root and os.path.normpath(root) != os.path.normpath(str(path)):
            return                                           # (the page looks elsewhere: its count stands)
        if not root:
            self.root.setText(str(path))
        self.scan(quiet=True)

    def searching(self, on):
        """While a look runs: Find Albums and Choose… are off and the toolbar shows it is busy."""
        self.scanning = on
        self.find_btn.setEnabled(not on)
        self.choose_btn.setEnabled(not on)
        if self.win is None or self.quiet:
            if not on:
                self.quiet = False
            return
        ov = self.win.pages.get("Overview")
        if ov is not None and getattr(ov, "running", False):
            return                                           # (the bar is the preview's)
        self.win.progress.setMaximum(0 if on else 1)         # (a busy bar: how many is not known)
        self.win.progress.setValue(0)
        self.win.show_progress(on)
        if on:
            self.win.statusBar().showMessage(tr("Looking for albums that need attention…"))
        else:
            self.win.statusBar().clearMessage()

    def scan_failed(self, msg):
        quiet = self.quiet
        self.searching(False)
        self.looks += 1
        if quiet:                                            # (a look for the badge: the page stays as it was)
            return
        self.empty.set_text(tr("The folder could not be read."), msg.splitlines()[0] if msg else "")
        self.views.setCurrentIndex(0)

    def show_rows(self, rows):
        if self.scanning:
            self.searching(False)
        self.looks += 1                                      # (an older look still running is dropped)
        self.rows = list(rows)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            # the reason in words (the codes are in the tooltip and the inspector's Codes)
            codes = list(r["codes"]) or ["other"]
            more = f" and {plural(len(codes) - 1, tr('more reason'), tr('more reasons'))}" if len(codes) > 1 else ""
            it = status_cell(QTableWidgetItem(), "unresolved",
                             f"{short_reason(codes[0])}{more}")
            tip = [reasons.describe(c).capitalize() for c in codes]
            if "missing_credentials" in codes:                     # (which key, and where to set it)
                from . import setup
                tip += setup.key_notices()
            it.setToolTip("\n".join(tip) + "\nCodes: " + ", ".join(codes))
            it.setData(Qt.UserRole, r)                              # (the row, for the inspector)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, folder_item(r["folder"], album=True))
            self.table.setItem(i, 2, QTableWidgetItem(r["reason"]))
        self.table.setSortingEnabled(True)
        self.fit_reason()
        if not rows:
            self.empty.set_text(tr("Nothing needs attention here."),
                                tr("No album in this folder is marked Unresolved."))
        self.views.setCurrentIndex(1 if rows else 0)
        self.report_btn.setEnabled(bool(rows))
        self.win.set_badge("Needs attention", len(rows))

    def row(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 and self.table.selectionModel().hasSelection() else None
        return it.data(Qt.UserRole) if it else None

    def selected_changed(self):
        r = self.row()
        if r:
            self.win.inspector.show_row(r)
        for b in self.buttons:
            b.setEnabled(bool(r))
        self.win.update_album_actions()

    def folder(self):
        r = self.table.currentRow()
        it = self.table.item(r, 1) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def retry(self):
        f = self.folder()
        if not f:
            return
        ov = self.win.pages["Overview"]
        if ov.running:                                      # (set_path says so too; stay on this page)
            self.win.statusBar().showMessage(tr("Finish or cancel the current work first."), 4000)
            return
        self.win.go("Overview")
        ov.set_path(f)

    def show_report(self):
        """The report of the albums listed (gui.report): Partial ones need your decision, Unresolved
        ones could not be finished, each with the releases its last plan suggested."""
        if self.win is not None and self.rows:
            from . import report
            if self.win.stack.currentWidget() is not self:
                self.win.go("Needs attention")
            return report.for_window(self.win)
        return None

    def web(self):
        f = self.folder()
        if f:
            search_web({"source_dir": f})

    def open(self):
        f = self.folder()
        if f:
            open_folder(f)
