"""gui/search.py - Picard's search dialogs and "Other versions", as sheets of the window.

- SearchSheet: search MusicBrainz releases, recordings and artists, and Discogs releases, by
  text; "Use This Release" plans the album again with the release chosen.
- VersionsSheet: every release of the album's MusicBrainz release group and Discogs master,
  each with how it agrees with the files (match_core.score_candidate, read only). In pressing
  mode (an Album only or Partial album) only the tied pressings, the details that differ
  highlighted and a hint of what to check on your copy.

Nothing is written here: a chosen release goes through force_release, the same forced-release
flow as Album > Change Release > Paste a Release Link (Overview.choose_release), and the album is previewed
again; Apply writes it as any plan. Lookups run on workers (gui.work.start).

Entry points for the window: open_search(page, album=None, text=None),
open_versions(page, album=None), open_pressing(page, album=None), can_choose_pressing(album)
and force_release(page, album, ref). `page` is the Overview (selected(), forced, preview(),
profile, win). The Album menu's commands: COMMANDS (gui.actions.COMMANDS' form, shortcuts that
leave Ctrl+F to Find), MENU (after use_release), handlers(ov) and update_commands(commands, page,
ready)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTabBar, QTableView, QVBoxLayout)

from . import icons, sheets, style, versions
from .common import heading, left_header, say
from .i18n import N_, tr
from .work import start

SEARCH_KINDS = (("release", N_("MusicBrainz Releases")), ("recording", N_("Recordings")), ("artist", N_("Artists")),
                ("discogs", N_("Discogs Releases")))
RECORDING_COLS = (("recording", N_("Recording")), ("length", N_("Length")), ("artist", N_("Artist")),
                  ("title", N_("On release")),
                  ("date", N_("Date")), ("country", N_("Country")), ("format", N_("Format")), ("tracks", N_("Tracks")))
ARTIST_COLS = (("name", N_("Name")), ("type", N_("Type")), ("country", N_("Country")), ("years", N_("Years")),
               ("about", N_("About")))
SORT_ROLE = Qt.UserRole + 1
# a narrow sheet (960 px) never scrolls sideways (_fit_columns): the room left goes to the first
# FLEX column there is; when there is too little, the SHRINK columns are shortened first (their text
# elided), then the DROP columns hidden in order (their values move into the row's tooltip; a value
# that tells pressings apart is never hidden), then "Agrees with your files", then every column
FLEX = ("fit", "title", "recording", "name", "about", "artist", "label")
SHRINK = ("artist", "label", "title", "about", "name", "recording")
DROP = ("country", "label", "format", "years", "type", "artist", "title")
FLEX_MIN = 96
FIT_CAP = 420                        # "Agrees with your files" at its widest: the reason to open the sheet
WHOLE = ("date", "score", "tracks")  # short values never shortened while another column can give
FIT_KEEP = 320                       # ... and what it keeps before the other columns are shortened


# --------------------------------------------------------------------------
# the forced-release flow, for an album of the Overview
# --------------------------------------------------------------------------
BUSY = N_("Finish or cancel the current work first.")
# after a release is chosen: how to go back (Undo Edit does not take a chosen release back)
CHOSEN = (N_("Previewing {name} again with the release you chose; nothing is written until you apply. To go "
             "back, choose another release (Album > Change Release)."))


def force_release(page, album, ref) -> bool:
    """Plan `album` again with the release `ref` (a MusicBrainz ID or "discogs:123"), as
    Overview.choose_release does with a pasted link: the choice is kept for this album only
    (a folder may hold several), and the album is previewed again. False when nothing ran."""
    import engine
    if not album or not ref or engine.parse_release_ref(ref)[0] is None:
        return False
    if getattr(page, "running", False):
        page.win.statusBar().showMessage(tr(BUSY), 4000)
        return False
    key = os.path.realpath(album["source_dir"])
    files = engine.plan_entry_files(album)
    mine = {os.path.realpath(f) for f in files}
    choices = [c for c in (page.forced.get(key) or []) if isinstance(c, dict)]
    was = next((c for c in choices if mine & {os.path.realpath(f) for f in c.get("files") or []}), None)
    choices = [c for c in choices if c is not was] + [{"ref": ref, "files": files}]
    page.forced[key] = choices
    page.preview()
    win = getattr(page, "win", None)
    if win is not None and hasattr(win, "statusBar"):
        win.statusBar().showMessage(tr(CHOSEN).format(name=Path(album["source_dir"]).name), 10000)
    return True


def can_choose_pressing(album) -> bool:
    """For the Album menu: the album's pressing is tied (Album only) or unproven (Partial)."""
    return bool(album) and versions.pressing_tied(album)


def _album(page, album):
    if album is not None:
        return album
    sel = getattr(page, "selected", None)
    return sel() if callable(sel) else None


def _profile(page):
    box = getattr(page, "profile", None)
    return box.currentText() if box is not None and hasattr(box, "currentText") else "default"


def _keep(page, attr, dlg):
    """One sheet of each kind per page, reused (its worker may still be finishing)."""
    old = getattr(page, attr, None)
    if old is not None:
        try:
            old.stop()
            old.close()
        except RuntimeError:                           # (already deleted with its window)
            pass
    setattr(page, attr, dlg)
    dlg.open()                                         # (window-modal, never blocks the caller)
    return dlg


def open_search(page, album=None, text=None):
    """The search sheet for the selected album (or `album`), its text searched at once."""
    album = _album(page, album)
    return _keep(page, "_search_sheet", SearchSheet(page, album, text))


def open_versions(page, album=None):
    """Other versions of the selected album (or `album`)."""
    return _keep(page, "_versions_sheet", VersionsSheet(page, _album(page, album)))


def open_pressing(page, album=None):
    """The pressing chooser: the tied pressings of an Album only or Partial album."""
    return _keep(page, "_versions_sheet", VersionsSheet(page, _album(page, album), pressing=True))


# The Album menu's commands, as gui.actions.COMMANDS (Ctrl+F stays Find: it searches the page shown)
COMMANDS = {
    "search_release": (N_("&Search MusicBrainz and Discogs…"), "Ctrl+Shift+F", "search",
                       N_("Search MusicBrainz and Discogs by words, barcode or catalog number")),
    "other_versions": (N_("Other &Versions of This Album…"), "Ctrl+Shift+L", "library",
                       N_("Every release of this album, and how each agrees with your files")),
    "choose_pressing": (N_("&Choose Your Pressing…"), "Ctrl+Shift+P", "list-checks",
                        N_("Pick the pressing you own when more than one fits your files")),
}
MENU = tuple(COMMANDS)


def handlers(ov):
    """The commands' actions; ov() gives the Overview."""
    return {"search_release": lambda: open_search(ov()), "other_versions": lambda: open_versions(ov()),
            "choose_pressing": lambda: open_pressing(ov())}


def update_commands(commands, page, ready):
    """Enable the commands as use_release: `ready` is the Overview shown, an album selected and
    nothing running; Choose Your Pressing also needs the album's pressing tied or unproven."""
    album = _album(page, None) if ready else None
    for k in ("search_release", "other_versions"):
        commands[k].setEnabled(bool(ready))
    commands["choose_pressing"].setEnabled(bool(ready) and can_choose_pressing(album))


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------
class ReleaseModel(QAbstractTableModel):
    """Rows of gui.versions dicts under the given (key, title) columns; `highlight` columns are
    bold on a faint accent tint (the details that tell tied pressings apart: not a status, so
    no status colour or icon)."""

    def __init__(self, cols):
        super().__init__()
        self.cols, self.rows, self.highlight = tuple(cols), [], set()
        self.hidden = set()                              # (keys of columns out of view: in the tooltip)

    def set_rows(self, rows, cols=None, highlight=()):
        self.beginResetModel()
        if cols is not None:
            self.cols = tuple(cols)
        self.rows, self.highlight = list(rows), set(highlight)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def key(self, c):
        return self.cols[c][0]

    def text(self, row, key):
        v = row.get(key)
        if key == "source":
            return versions.SOURCES.get(v, v or "")
        if key == "score":
            return "" if v is None else f"{v:.0f}"
        if key == "barcode":
            return barcode_text(v)
        if key == "fit" and row.get("score") is None and row.get("source") == "discogs" and "score" in dict(self.cols):
            return tr("Not compared")
        return "" if v is None else str(v)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o != Qt.Horizontal or s >= len(self.cols):
            return None
        key = self.cols[s][0]
        if role == Qt.DisplayRole:
            return tr(self.cols[s][1])
        if role == Qt.FontRole and key in self.highlight:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ToolTipRole and key in self.highlight:
            return tr("Differs between these pressings: check it on your copy")
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        row, key = self.rows[idx.row()], self.key(idx.column())
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            t = self.text(row, key)
            if role == Qt.AccessibleTextRole and key in self.highlight:
                t += " (differs)"
            return t
        if role == Qt.ToolTipRole:
            if key in ("fit", "score") and row.get("notes"):
                return "\n".join(row["notes"])
            tip = self.text(row, key) + (tr(" (differs between these pressings)") if key in self.highlight else "")
            more = [f"{title}: {self.text(row, k)}" for k, title in self.cols if k in self.hidden and self.text(row, k)]
            return "\n".join(x for x in [tip.strip()] + more if x) or None
        if role == SORT_ROLE:
            v = row.get(key)
            if key in ("score", "tracks"):
                return -1.0 if v is None else float(v)
            return self.text(row, key).casefold()
        if role == Qt.UserRole:
            return row
        if role == Qt.TextAlignmentRole and key in ("tracks", "score"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if key in self.highlight:
            if role == Qt.FontRole:
                f = QFont()
                f.setBold(True)
                return f
            if role == Qt.BackgroundRole:
                return tint()
        return None


def barcode_text(v) -> str:
    """Barcodes as one column shows them: digits only ("5 012345 678917" from Discogs reads as
    MusicBrainz's "5012345678917"); one with letters in it is left as it is."""
    out = []
    for part in str(v or "").split(";"):
        part = part.strip()
        bare = part.replace(" ", "").replace("-", "")
        out.append(bare if bare.isdigit() else part)
    return "; ".join(p for p in out if p)


class ResultTable(QTableView):
    """A results table that says, centred in it, what to do while it is empty (placeholder)."""

    placeholder = ""

    def paintEvent(self, e):
        super().paintEvent(e)
        m = self.model()
        if self.placeholder and m is not None and not m.rowCount():
            p = QPainter(self.viewport())
            p.setPen(QColor(style.color("muted")))
            p.setFont(style.font("body"))
            r = self.viewport().rect().adjusted(style.SPACE["l"], style.SPACE["l"], -style.SPACE["l"],
                                                -style.SPACE["l"])
            p.drawText(r, int(Qt.AlignCenter | Qt.TextWordWrap), self.placeholder)
            p.end()


def tint() -> QColor:
    """A value to compare: the accent at about 12% over the row (ink text stays ink)."""
    c = QColor(style.color("accent"))
    c.setAlpha(30)
    return c


def _view(model, name):
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    t = ResultTable()
    t.setModel(proxy)
    t.setSortingEnabled(True)
    # (the order the lookup gives, its relevance, until a header is clicked: no arrow shown)
    t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setTextElideMode(Qt.ElideRight)
    t.setWordWrap(False)
    t.setAccessibleName(name)
    left_header(t)
    h = t.horizontalHeader()
    h.setMinimumSectionSize(48)
    h.setSectionResizeMode(QHeaderView.Interactive)
    h.setStretchLastSection(False)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)     # (the columns fit: _fit_columns)
    t._proxy = proxy
    return t


def _fit_columns(table, wide=("title", "artist", "fit", "recording", "name", "about")):
    """Every column in view, never a sideways scroll (see FLEX, SHRINK and DROP): each as wide
    as its contents (the long text ones capped), then narrowed or hidden to fit the table."""
    m = table._proxy.sourceModel()
    for c in range(m.columnCount()):
        table.setColumnHidden(c, False)
    m.hidden = set()
    if not m.columnCount():
        return
    table.resizeColumnsToContents()
    want = {}
    for c in range(m.columnCount()):
        cap = FIT_CAP if m.key(c) == "fit" else 260 if m.key(c) in wide else 160
        # (room for bold text and the sort arrow)
        want[c] = max(min(table.columnWidth(c) + style.SPACE["m"] + (8 if m.key(c) in m.highlight else 0), cap), 72)
    room = table.viewport().width() or table.width()
    shown = lambda: [c for c in want if not table.isColumnHidden(c)]  # noqa: E731
    over = lambda: sum(want[c] for c in shown()) - room  # noqa: E731

    def narrow(cols, floor):
        for c in cols:
            if over() <= 0:
                return
            want[c] = max(min(want[c], floor), want[c] - over())
    full = dict(want)
    shrink = [c for k in SHRINK for c in want if m.key(c) == k]
    narrow(shrink, FLEX_MIN)
    for k in DROP:
        if over() <= 0:
            break
        for c in want:
            if m.key(c) == k and k not in m.highlight and len(shown()) > 2:
                table.setColumnHidden(c, True)
                m.hidden.add(k)
    if m.hidden:                                         # (the room a hidden column left: the shortened
        for c in shrink:                                 #  ones whole again, as far as it goes)
            want[c] = full[c]
        narrow(shrink, FLEX_MIN)
    narrow([c for c in want if m.key(c) == "fit"], FIT_KEEP)   # (its first words: most rows whole)
    if over() > 0:                                       # (narrower still: every column but "Agrees with
        cols = [c for c in shown() if m.key(c) != "fit"]  # your files" gives a share, the reason
        extra = over()                                   # to open the sheet; it gives last; a date
        give = {c: 0 if m.key(c) in WHOLE else max(0, want[c] - 72) for c in cols}   # or a number stays whole)
        total = sum(give.values()) or 1
        for c in cols:
            want[c] -= min(give[c], -(-extra * give[c] // total))
    narrow([c for c in want if m.key(c) == "fit"], FLEX_MIN)
    if over() > 0:                                       # (narrower still: every column gives a share)
        cols, extra = shown(), over()
        give = {c: max(0, want[c] - 48) for c in cols}
        total = sum(give.values()) or 1
        for c in cols:
            want[c] -= min(give[c], -(-extra * give[c] // total))
    first = next((c for k in FLEX for c in shown() if m.key(c) == k), None)
    if first is not None and over() < 0:
        want[first] -= over()                            # (the room left: the first FLEX column's)
    for c in shown():
        table.setColumnWidth(c, want[c])


def lookup_error(msg) -> str:
    """A failed lookup in plain words: who could not be reached, and the first line of why."""
    first = (msg or "").strip().splitlines()[0] if (msg or "").strip() else ""
    low = first.lower()
    where = "Discogs" if "discogs" in low else "MusicBrainz"
    if any(w in low for w in ("urlopen", "timed out", "timeout", "connection", "name or service", "network",
                              "unreachable", "temporary failure", "http error 5", "http error 429")):
        return tr("{where} could not be reached: {first}").format(where=where, first=first)
    return tr("The lookup did not finish: {first}").format(first=first) if first else tr("The lookup did not finish.")


class _Base(QDialog):
    """A large sheet: heading, a table, a status line and the button row (Close, then the default)."""

    ACTION = N_("Use This Release")

    STATUS_NAME = N_("Search status")

    def __init__(self, page, album, title, hint, cols, table_name):
        super().__init__(sheets._window(page))
        sheets.prepare(self, title)
        self.page, self.album = page, album
        self.worker = None
        self.setMinimumSize(640, 420)
        self.resize(980, 560)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        self.lay.setSpacing(style.SPACE["s"] + 4)
        self.head = heading(title, hint)
        self.lay.addLayout(self.head)
        self.top = QVBoxLayout()
        self.top.setSpacing(style.SPACE["s"])
        self.lay.addLayout(self.top)
        self.model = ReleaseModel(cols)
        self.table = _view(self.model, table_name)
        self.table.doubleClicked.connect(lambda *_: self.use())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.update_buttons())
        self.lay.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        self.status.setAccessibleDescription(tr(self.STATUS_NAME))
        self.say("")
        self.lay.addWidget(self.status)
        row, self.ok = sheets.button_row(self, tr(self.ACTION), self.use, cancel=tr("Close"))
        self.browser = QPushButton(tr("Open in Browser"))
        self.browser.setIcon(icons.icon("external-link", "ink"))
        self.browser.setAccessibleName(tr("Open the selected release in the browser"))
        self.browser.setAutoDefault(False)
        self.browser.clicked.connect(self.open_browser)
        row.insertWidget(0, self.browser)
        self.lay.addLayout(row)
        self.button_bar = row
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(self.refit)
        self.update_buttons()

    # ---- selection ---------------------------------------------------------
    def current(self):
        if not self.table.selectionModel().hasSelection():
            return None
        idx = self.table.currentIndex()
        return self.table._proxy.data(self.table._proxy.index(idx.row(), 0), Qt.UserRole) if idx.isValid() else None

    def can_use(self, row) -> bool:
        return bool(row and row.get("ref") and self.album)

    def update_buttons(self):
        row = self.current()
        self.ok.setEnabled(self.can_use(row))
        self.browser.setEnabled(bool(row and row.get("url")))
        if self.album is None:
            self.ok.setToolTip(tr("Select an album in Overview first"))
        self.lead(self.first_step())
        self.table.viewport().update()                   # (the empty table's words, shown or gone)

    def first_step(self):
        """The button to press next while the table is empty (Search, Look Up), else None: Use This
        Release, once there are rows to use."""
        return None

    def lead(self, button=None):
        """One prominent, default button: `button`, or Use This Release when None."""
        want = button if button is not None and button.isEnabled() else self.ok
        self._leads = getattr(self, "_leads", {self.ok}) | {want}
        for b in self._leads:
            on = b is want
            if b.isDefault() != on or (b.property("role") == "primary") != on:
                b.setDefault(on)
                b.setProperty("role", "primary" if on else "")
                b.style().unpolish(b)
                b.style().polish(b)

    def open_browser(self):
        row = self.current()
        if row and row.get("url"):
            QDesktopServices.openUrl(QUrl(row["url"]))

    def say(self, text):
        """The status line, read out as "Search status: text" when it changes."""
        say(self.status, tr(self.STATUS_NAME), text)

    def use(self):
        row = self.current()
        if not self.can_use(row):
            return
        if getattr(self.page, "running", False):        # (said here: the window's status bar is behind the sheet)
            self.say(tr(BUSY))
            return
        if force_release(self.page, self.album, row["ref"]):
            self.stop()
            self.accept()

    # ---- work --------------------------------------------------------------
    def run(self, fn, done, busy_text):
        self.stop()
        self.say(busy_text)
        profile = _profile(self.page)
        from .skeleton import Skeleton
        self._last_run = (fn, done, busy_text)
        sk = Skeleton.over(self.table)
        if not self.model.rowCount():                  # (an empty list: the rows' shape while they load)
            sk.start(cancel=self.stop_waiting)

        def finished(r):
            sk.stop()
            done(r)

        def work(w):
            if w.cancelled:                            # (replaced before it began: ask nothing)
                return None
            lk = versions.make_lookup(profile)
            try:
                return fn(lk, w)
            finally:
                lk.close()
        me = None

        def current():                                 # (an older run, answered late, is dropped)
            return me is not None and me is self.worker
        me = self.worker = start(self, work, lambda r: current() and r is not None and finished(r),
                                 lambda *a: current() and self.progress(*a),
                                 lambda msg: current() and self.failed(msg))

    def progress(self, i, n, what):
        if what:
            self.say(f"{what}…")

    AGAIN = N_("Press Return to search again.")

    def failed(self, msg):
        self.say(f"{lookup_error(msg)} {tr(self.AGAIN)}")
        from .skeleton import Skeleton
        if not self.model.rowCount():                  # (no bars left standing: what failed, and Try Again)
            Skeleton.over(self.table).fail(lookup_error(msg), retry=self.run_again)

    def run_again(self):
        """Try Again on the list: the sheet's own way of asking (a search: what the field says now)."""
        if getattr(self, "_last_run", None):
            self.run(*self._last_run)

    def stop_waiting(self):
        """Stop, from the skeleton of a look-up that is taking long: the empty list says so, and
        the answer, if it still comes, is dropped (not shown as found)."""
        self.stop()
        self.worker = None
        from .skeleton import Skeleton
        Skeleton.over(self.table).fail(tr("Stopped. Nothing was changed."), retry=self.run_again)

    def stop(self):
        if self.worker is not None:
            self.worker.cancelled = True

    def done(self, r):
        super().done(r)
        self.stop()

    def refit(self):
        if self.model.rowCount():
            _fit_columns(self.table)                     # (narrower or wider: every column in view again)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refit()
        if hasattr(self, "_refit"):
            self._refit.start()                          # (again once the layout has given the table its width)

    def showEvent(self, e):
        super().showEvent(e)
        self.refit()
        if hasattr(self, "_refit"):
            self._refit.start()

    def keyPressEvent(self, e):
        # (Return in the search field searches; it never takes the release by surprise)
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(self.focusWidget(), QLineEdit):
            return
        super().keyPressEvent(e)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
class SearchSheet(_Base):
    def __init__(self, page, album=None, text=None):
        name = Path(album["source_dir"]).name if album else ""
        hint = (tr("Find the release of {name}. Choosing one previews the album again with it; nothing is "
                   "written until you apply.").format(name=name) if album else
                tr("Search MusicBrainz and Discogs. Select an album in Overview to use a release for it."))
        super().__init__(page, album, tr("Search for Release"), hint, versions.COLS, tr("Search results"))
        self._artist = None                              # (id, name): the artist whose releases are listed
        bar = QHBoxLayout()
        bar.setSpacing(style.SPACE["s"])
        self.query = QLineEdit(text if text is not None else (versions.query_for(album) if album else ""))
        self.query.setPlaceholderText(tr("Album, artist, barcode or catalog number"))
        self.query.setAccessibleName(tr("Search for"))
        self.query.setClearButtonEnabled(True)
        self.query.addAction(icons.icon("search", "muted"), QLineEdit.LeadingPosition)
        self.query.returnPressed.connect(self.search)
        self.query.textEdited.connect(self.typed)
        bar.addWidget(self.query, 1)
        self.go = QPushButton(tr("Search"))
        self.go.setAccessibleName(tr("Search"))
        self.go.setAutoDefault(False)
        self.go.clicked.connect(self.search)
        bar.addWidget(self.go)
        self.top.addLayout(bar)
        self.kinds = QTabBar()
        self.kinds.setDocumentMode(True)
        self.kinds.setDrawBase(False)
        self.kinds.setExpanding(False)                     # (compact and at the left, as the app's other tabs)
        self.kinds.setAccessibleName(tr("Search in"))
        for i, (_k, label) in enumerate(SEARCH_KINDS):
            self.kinds.addTab(tr(label))
            self.kinds.setTabToolTip(i, tr("{label} (Ctrl+{0})").format(i + 1, label=tr(label)))
        self.kinds.currentChanged.connect(lambda *_: self.search())
        self.top.addWidget(self.kinds, 0, Qt.AlignLeft)
        for i in range(len(SEARCH_KINDS)):                 # Ctrl+1 … Ctrl+4, as the inspector's tabs
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.kinds.setCurrentIndex(i))
        self.results = {}
        self.query.setFocus()
        if self.query.text().strip():
            self.search()
        else:
            self.say(tr("Type what to search for, then press Return."))

    def kind(self):
        return SEARCH_KINDS[self.kinds.currentIndex()][0]

    def cols(self, kind):
        return {"recording": RECORDING_COLS, "artist": ARTIST_COLS}.get(kind, versions.COLS)

    def typed(self, _text):
        self._artist = None                              # (your own words now, not the artist's releases)

    def search_text(self) -> str:
        """What is searched: the field's words, or the chosen artist's releases (by their ID,
        which is never shown in the field)."""
        text = self.query.text().strip()
        if self._artist and text == self._artist[1]:
            return f"arid:{self._artist[0]}"
        return text

    def search(self):
        kind, text = self.kind(), self.search_text()
        self.model.set_rows([], self.cols(kind))
        self.update_buttons()
        if not text:
            from .skeleton import Skeleton
            Skeleton.over(self.table).stop()               # (no earlier failure's words left over the list)
            self.say(tr("Type what to search for, then press Return."))
            return
        where = "Discogs" if kind == "discogs" else "MusicBrainz"
        self.run(lambda lk, w: dict(versions.search(lk, kind, text), kind=kind, text=text),
                 self.found, tr("Searching {where}…").format(where=where))

    def run_again(self):
        self.search()                                      # (what the field and the tab say now)

    def found(self, res):
        if res.get("kind") != self.kind() or res.get("text") != self.search_text():
            return                                         # (an older search, answered late)
        self.results[res["kind"]] = res
        rows = res["rows"]
        self.model.set_rows(rows, self.cols(res["kind"]))
        _fit_columns(self.table)
        what = {"recording": ("recording", "recordings"), "artist": ("artist", "artists")}.get(
            res["kind"], ("release", "releases"))
        if res.get("note"):
            self.say(res["note"])
        elif not rows:
            self.say(tr("Nothing found. Try fewer words, or the barcode or catalog number on the case."))
        else:
            by = f" by {self._artist[1]}" if self._artist and res["text"].startswith("arid:") else ""
            self.say(f"{len(rows)} {what[0] if len(rows) == 1 else what[1]}{by} found."
                                + (tr(" Select one, then Use This Release.") if self.album and res["kind"] != "artist"
                                   else ""))
        if rows:
            self.table.selectRow(0)
        self.update_buttons()

    def first_step(self):
        go = getattr(self, "go", None)
        return go if go is not None and not self.model.rowCount() else None

    def update_buttons(self):
        super().update_buttons()
        artist = hasattr(self, "kinds") and self.kind() == "artist"
        self.ok.setText(tr("Show Releases") if artist else tr(self.ACTION))
        self.ok.setAccessibleName(tr("Show the artist's releases") if artist else tr(self.ACTION))

    def can_use(self, row):
        if hasattr(self, "kinds") and self.kind() == "artist":
            return bool(row and row.get("id"))
        return super().can_use(row)

    def use(self):
        row = self.current()
        if hasattr(self, "kinds") and self.kind() == "artist":
            if row and row.get("id"):                     # (an artist: their releases, in the releases tab)
                self._artist = (row["id"], row.get("name") or row["id"])
                self.query.setText(self._artist[1])
                if self.kinds.currentIndex() == 0:
                    self.search()
                else:
                    self.kinds.setCurrentIndex(0)
            return
        super().use()


# --------------------------------------------------------------------------
# Other versions and the pressing chooser
# --------------------------------------------------------------------------
class VersionsSheet(_Base):
    STATUS_NAME = N_("Lookup status")
    AGAIN = N_("Choose Try Again (Ctrl+R) to look again.")

    def __init__(self, page, album, pressing=False):
        self.pressing = pressing
        name = Path(album["source_dir"]).name if album else tr("this album")
        if pressing:
            title = tr("Choose Your Pressing")
            hint = (tr("Pick the pressing of {name} you own: the album is previewed again with its MusicBrainz "
                       "or Discogs release, catalog number and barcode.").format(name=name))
        else:
            title = tr("Other Versions")
            hint = (tr("Every release of {name} on MusicBrainz and Discogs, and how each agrees with your "
                       "files. Choosing one previews the album again with it.").format(name=name))
        self.ACTION = N_("Use This Pressing") if pressing else N_("Use This Release")
        # (how each agrees with the files first: the reason to open this sheet)
        super().__init__(page, album, title, hint, versions.COLS[:1] + versions.FIT_COLS + versions.COLS[1:],
                         tr("Pressings") if pressing else tr("Versions"))
        self.columns = self.model.cols
        self.check = QLabel("")
        self.check.setProperty("role", "headline")
        self.check.setWordWrap(True)
        self.check.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.check.setAccessibleName(tr("What to check on your copy"))
        self.check.hide()
        self.top.addWidget(self.check)
        self.legend = QLabel(tr("Bold values are the ones to compare with your copy."))
        self.legend.setProperty("role", "caption")
        self.legend.hide()
        self.top.addWidget(self.legend)
        self.data = None
        self.again = QPushButton(tr("Try Again"))
        self.again.setAccessibleName(tr("Look up the album's releases again"))
        self.again.setToolTip(tr("Look up the album's releases again (Ctrl+R)"))
        self.again.setAutoDefault(False)
        self.again.clicked.connect(self.reload)
        self.again.hide()                                  # (shown when a lookup fails)
        self.button_bar.insertWidget(1, self.again)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.reload)
        if album is None:
            self.say(tr("Select an album in Overview first."))
            return
        self.reload()

    def failed(self, msg):
        super().failed(msg)
        self.again.show()

    def run_again(self):
        self.reload()

    def reload(self):
        album = self.album
        if album is None:
            return
        self.again.hide()
        self.run(lambda lk, w: dict(versions.versions(lk, album, w.progress.emit, lambda: w.cancelled), cfg=lk.cfg),
                 self.listed, tr("Looking up the album's releases…"))

    def listed(self, res):
        self.data = res
        rows, notes = res["rows"], list(res.get("notes") or [])
        highlight, equal = [], False
        if self.pressing:
            tied = versions.tied(rows, res.get("cfg") or {}, res.get("media") or "")
            if len(tied) >= 2:
                rows, equal = tied, True
                highlight = versions.differing(rows)
                self.check.setText(versions.hint(rows))
                self.check.show()
                self.legend.setVisible(bool(highlight))
            else:
                notes.insert(0, tr("No pressings tie on your files now: every version is listed, best fit first."))
        cols = self.columns
        if equal:                                        # (every pressing fits equally: no Fit columns,
            cols = tuple(c for c in cols if c not in versions.FIT_COLS)   # the status line says so once)
        if highlight:                                    # (what tells them apart first; the title and
            first = sorted((c for c in cols if c[0] in highlight),        # artist, the same on all, last)
                           key=lambda c: versions.DIFF_ORDER.index(c[0]))
            cols = cols[:1] + tuple(first) + tuple(c for c in cols[1:] if c not in first and c[0] not in
                                                   ("title", "artist")) + tuple(c for c in cols if c[0] in
                                                                                ("title", "artist"))
        self.model.set_rows(rows, cols, highlight=highlight)
        _fit_columns(self.table)
        if ("score", "Fit") in self.model.cols:
            self.table.sortByColumn(self.model.cols.index(("score", "Fit")), Qt.DescendingOrder)
        else:
            self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        n = res.get("n_files") or 0
        head = ((tr("Both pressings fit your {n} files equally.").format(n=n) if len(rows) == 2 else
                 tr("These {0} pressings fit your {n} files equally.").format(len(rows), n=n))
                if equal else
                tr("{0} release{1} found.").format(len(rows), 's' if len(rows) != 1 else '') if rows else
                tr("No other versions found. Search for the release instead (Album > Change Release > Search "
                   "MusicBrainz and Discogs…, {0}).").format(COMMANDS['search_release'][1]))
        self.say(" ".join([head] + notes))
        if rows:
            self.table.selectRow(0)
        self.update_buttons()
