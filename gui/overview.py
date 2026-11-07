"""gui/overview.py - The Overview page: drop or choose a folder, pick a profile, Preview, then Apply."""
from __future__ import annotations

import html
import os
import time
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QStackedWidget, QStyledItemDelegate, QTableView, QVBoxLayout,
                               QWidget)

from . import icons, setup, sheets
from .common import (album_words, counts_by_label, counts_text, ex, heading, left_header, load_profile, notify,
                     pf, planmod, plural, popup_menu, profile_names)
from .i18n import N_, tr
from .work import start
from .models import PlanModel, can_apply
from .widgets import ElidedPath as _Elided, SleeveDrop

APPLY_LISTED = 5                                     # albums named in the Apply sheet (then "and N more")


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def run_report(plan, res):
    """The report of what Apply just did (report.build), shown once it is done; None when it
    cannot be made (the job still is, under Jobs)."""
    import time

    import report
    try:
        rep = report.build(plan, res["albums"], job={"status": res.get("status") or "done"})
    except Exception:
        return None
    rep["job"], rep["started"] = res.get("job") or rep["job"], time.time()
    return rep


# what an album that changed nothing ended as: a job of only these has nothing to undo
NOTHING_DONE = {"conflict", "failed", "cancelled", "unchanged"}


def merge_results(plans, run, cancelled=lambda: False) -> dict:
    """One Apply's result from running PLANS (one per profile, each a job of its own) in order:
    the albums' indexes count on through them. A plan whose run cannot start (another job is
    running or waits to be recovered) fails its albums; once cancelled, the rest wait."""
    jobs, counts, albums, off, partial = [], {}, [], 0, False
    for at, plan in enumerate(plans):
        n = len(plan["albums"])
        if cancelled():                                     # (the rest not started: counted cancelled,
            left = sum(len(p["albums"]) for p in plans[at:])  # as the executor counts its own)
            if left:
                counts["cancelled"] = counts.get("cancelled", 0) + left
                partial = True
            break
        try:
            res = run(plan)
        except Exception as exc:
            if len(plans) == 1:
                raise
            partial = True
            for k, a in enumerate(plan["albums"]):
                albums.append({"index": off + k, "album": a["source_dir"], "status": "failed",
                               "label": "Failed", "detail": str(exc).splitlines()[0] if str(exc) else ""})
            counts["failed"] = counts.get("failed", 0) + n
            off += n
            continue
        jobs.append(res["job"])
        partial = partial or res.get("status") != "done"
        for k, v in (res.get("counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
        for r in res.get("albums") or []:
            albums.append(dict(r, index=off + r["index"]) if "index" in r else r)
        off += n
    return {"job": ", ".join(jobs), "status": "partial" if partial else "done", "counts": counts, "albums": albums}


def plan_each(e, path, keys, emit, progress=None, cancel=None, meta=None) -> int:
    """Engine E's plan of KEYS an album at a time (engine.Engine.plan_each)."""
    return e.plan_each(path, keys, emit, progress=progress, cancel=cancel, meta=meta)


# Look Up's sources (List first): the profile's, or one of them alone
LOOK_UP = (("profile", N_("Profile's sources")), ("musicbrainz_only", N_("MusicBrainz only")),
           ("musicbrainz_preferred", N_("MusicBrainz preferred")), ("discogs_preferred", N_("Discogs preferred")),
           ("discogs_only", N_("Discogs only")), ("web", N_("Web only")))


# The files' metadata as List first, Look Up and Preview last read it (ex.meta_memo): an
# unchanged file is not opened again to find the albums. Handed to the engine explicitly (its
# work runs on worker threads, where a context variable starts empty).
META_STORE: dict = {}


# what is being done with an album now, in its Status column (the page's, never the plan's)
ROW_STATES = {"waiting": N_("Waiting…"), "looking": N_("Looking up…"), "applying": N_("Applying…")}


RETRY_CODES = {"musicbrainz_unavailable", "discogs_unavailable", "web_unavailable", "rate_limited",
               "missing_credentials"}


def _list_first() -> bool:
    """Preferences › General › When you add a folder: List its albums first."""
    try:
        import app_settings
        return app_settings.get("add_folder") == "list"
    except Exception:
        return False


def profile_for(prof, mode):
    """PROF as Look Up uses it: as it is ("profile"), or asking only MusicBrainz, only Discogs, or
    no database but the web (its store and encyclopaedia pages)."""
    if mode == "profile":
        return prof
    import copy
    d = copy.deepcopy(prof.data)
    prov = d.setdefault("providers", {})
    if mode in ("musicbrainz", "discogs"):                 # (older names: one database alone)
        mode = f"{mode}_only"
    if mode in pf.SOURCES:                                  # (the four Sources choices of a profile)
        d["source"] = mode
        for n in ("musicbrainz", "discogs"):
            if mode.startswith(n) or mode.endswith("preferred"):
                prov.setdefault(n, {}).update(enabled=True, identify=True)
    elif mode == "web":
        for n in ("musicbrainz", "discogs"):
            prov.setdefault(n, {})["identify"] = False
        prov.setdefault("web", {})["enabled"] = True
    return pf.from_dict(d)


def library_for(prof, folder):
    """PROF with FOLDER as the library albums go to (this preview and Apply only): the profile's
    destination and every source folder's own destination (which would win over it) replaced."""
    if not folder:
        return prof
    import copy
    d = copy.deepcopy(prof.data)
    d["destination"] = folder
    for s in d.get("sources") or []:
        if isinstance(s, dict):
            s.pop("destination", None)
    return pf.from_dict(d)


class ProfileDelegate(QStyledItemDelegate):
    """An album's profile in its row: drawn as a drop-down, and one when clicked (the first
    choice, As Above, is the profile chosen above the list)."""

    ARROW = 22                                             # (room for the drop-down's arrow)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not index.flags() & Qt.ItemIsEditable:
            return
        from PySide6.QtWidgets import QStyle
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.placeholderText().color())
        r = option.rect.adjusted(option.rect.width() - self.ARROW, 0, -6, 0)
        painter.drawText(r, int(Qt.AlignRight | Qt.AlignVCenter), "▾")
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        page = self.parent()
        words = [tr("As Above ({0})").format(page.profile.currentText())] + list(profile_names())
        widest = max(option.fontMetrics.horizontalAdvance(w) for w in words)
        hint.setWidth(max(hint.width(), widest + 40) + self.ARROW)
        return hint

    def createEditor(self, parent, option, index):
        from .rows import Popup
        box = Popup(parent)
        box.setAccessibleName(tr("Profile for this album"))
        page = self.parent()
        box.addItem(tr("As Above ({0})").format(page.profile.currentText()), "")
        for name in profile_names():
            box.addItem(name, name)
        box.activated.connect(lambda _i, b=box: (self.commitData.emit(b), self.closeEditor.emit(b)))
        return box

    def setEditorData(self, editor, index):
        i = editor.findData(index.data(Qt.EditRole) or "")
        editor.setCurrentIndex(max(0, i))
        QTimer.singleShot(0, editor.showPopup)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData() or "", Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class Overview(QWidget):
    """The profile picker, search field, progress bar and Preview / Apply / Cancel live in the
    window's toolbar (the window makes them); the album actions (Change Release: a link, Search for
    Release, Other Versions, Choose Your Pressing, Info, Tags from File Names ...) in the Album and
    Tools menus, the table's context menu and the inspector's header."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plan = None
        self.path = None
        self.worker = None
        self.forced = {}
        self.library = ""           # the library folder chosen for this preview ("": the profile's)
        self.looked = {}            # album key -> how it was looked up (profile, sources): not again
        self.removed = set()        # folders of albums taken out of the list (Remove from List)
        # several albums, each with a profile of its own: the one chosen above unless you chose
        # another in its row (album key -> profile name); the profile each album's preview was
        # made with, and that preview's plan (without its albums) per profile, for Apply
        self.album_profiles = {}
        self.planned_with = {}
        self.headers = {}
        self.used_jobs = set()      # (jobs Apply ran: the next Apply of the same preview is a new job)
        self._lookup_n = 0
        # Look Up while a look-up runs: album key -> (sources, again), looked up when it ends;
        # and the albums the running look-up has (album key -> (profile, sources))
        self.queue = {}
        self.looking = {}
        # folders dropped (or chosen with Add Folder) while work runs: their albums join this list
        # when it ends, and are looked up then (List first: listed only)
        self.later = []
        self._later_list_only = False
        # album key -> the folder its look-up reads, for albums outside the folder previewed (a
        # folder dropped meanwhile, an album already applied and given another profile)
        self.roots = {}
        # what is being done with each album now (album key, or id() of a row Apply has): ROW_STATES
        self.states = {}
        self._lookup_order, self._applying = [], False
        self._lookup_i, self._lookup_name = 0, ""
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("Overview")))              # (what to do is the list and Apply: no subtitle)
        self.profile = win.profile_box
        self.search = win.search_box
        self.bar = win.progress
        self.btn_preview, self.btn_apply, self.btn_cancel = (win.act(k) for k in ("preview", "apply", "cancel"))
        self.btn_anyway, self.btn_web, self.btn_open, self.btn_release = (
            win.act(k) for k in ("process_anyway", "search_web", "open_folder", "use_release"))
        self.views = QStackedWidget()
        lay.addWidget(self.views, 1)
        self.drop = SleeveDrop()
        self.drop.dropped.connect(self.set_path)
        self.views.addWidget(self.drop)                     # the empty state fills the page
        self.results = QWidget()
        rl = QVBoxLayout(self.results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        strip = QFrame()
        strip.setObjectName("strip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)              # (no box: the table below is the one frame)
        self.folder_icon = QLabel()
        self.folder_icon.setPixmap(icons.icon("folder-open", "muted").pixmap(16, 16))
        sl.addWidget(self.folder_icon)
        self.where = _Elided(tr("No folder chosen"))
        sl.addWidget(self.where, 1)
        self.ocr = QCheckBox(tr("Read scans"))
        self.ocr.setAccessibleName(tr("Read scans for this preview"))
        self.ocr.setToolTip(tr("Read barcodes and catalog numbers from cover scans of every album (slower). "
                               "Unticked, the profile's OCR setting decides (default: albums with scans or a PDF)"))
        sl.addWidget(self.ocr)
        self.choose_btn = ch = QPushButton(tr("Choose…"))
        ch.setAccessibleName(tr("Choose another folder"))
        ch.setToolTip(tr("Choose another folder (Ctrl+O)"))
        ch.clicked.connect(lambda: self.drop.choose())
        sl.addWidget(ch)
        # (0.8.6: neither is shown - the profile's "Read scans and booklets" decides which albums'
        # scans are read, and Add Folder (Ctrl+O) or a drop chooses the folder)
        self.ocr.setVisible(False)
        ch.setVisible(False)
        rl.addWidget(strip)
        # which databases to ask, and the button that asks them (nothing is looked up on a drop)
        look = QHBoxLayout()
        look.setContentsMargins(0, 0, 0, 0)
        self.sources_label = QLabel(tr("Sources"))
        self.sources_label.setProperty("role", "hint")       # (a label for the control beside it: quiet)
        look.addWidget(self.sources_label)
        from .rows import Popup
        from PySide6.QtWidgets import QComboBox

        class _Sources(Popup):
            """Its whole words when there is room; in a narrow window it gives way to 10 letters."""
            def minimumSizeHint(self):
                hint = super().minimumSizeHint()
                hint.setWidth(min(hint.width(), self.fontMetrics().averageCharWidth() * 10 + 48))
                return hint
        self.sources = _Sources()
        self.sources.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.sources.setAccessibleName(tr("Sources to look up with"))
        self.sources.setToolTip(tr("Which databases Look Up and Preview ask: the profile's own choice, or "
                                   "MusicBrainz, Discogs or the web (store and encyclopaedia pages) alone"))
        for mode, words in LOOK_UP:
            self.sources.addItem(tr(words), mode)
        look.addWidget(self.sources)
        self.look_btn = QPushButton(tr("Look Up"))
        self.look_btn.setAccessibleName(tr("Look up the selected albums, or all of them"))
        self.look_btn.setToolTip(tr("Looks up the selected albums, or every album when none is selected, with "
                                    "the sources chosen beside it. Nothing is written until you apply."))
        self.look_btn.clicked.connect(self.look_up_clicked)
        self.look_btn.setFlat(True)                          # (secondary: Apply, in the toolbar, is the action)
        look.addWidget(self.look_btn)
        # (Refresh: the selected albums - none selected: all - looked up again with the profile as
        # it is now, after you changed a setting; what was found before is replaced)
        self.refresh_btn = QPushButton(tr("Refresh"))
        self.refresh_btn.setAccessibleName(tr("Look up the selected albums again with the current settings"))
        self.refresh_btn.setToolTip(tr("Looks up the selected albums again (every album when none is selected) "
                                       "with the profile as it is now, after you changed a setting (Ctrl+R). "
                                       "Nothing is written until you apply."))
        self.refresh_btn.clicked.connect(self.reload)
        self.refresh_btn.setFlat(True)
        look.addWidget(self.refresh_btn)
        # (a choice other than the profile's is for this look-up only; this saves it into the profile)
        self.keep_link = QLabel(f'<a href="keep">{tr("Make It the Profile’s")}</a>')
        self.keep_link.setTextFormat(Qt.RichText)
        self.keep_link.setAccessibleName(tr("Make this choice of sources the profile's own"))
        self.keep_link.linkActivated.connect(lambda _h: self.keep_sources())
        self.keep_link.hide()
        look.addWidget(self.keep_link)
        look.addStretch(1)
        rl.addLayout(look)
        self.stale_note = QLabel("")                         # (settings changed since the look-up)
        self.stale_note.setProperty("role", "attention")
        self.stale_note.setTextFormat(Qt.RichText)
        self.stale_note.setWordWrap(True)
        self.stale_note.linkActivated.connect(lambda _h: self.refresh_stale())
        self.stale_note.hide()
        rl.addWidget(self.stale_note)
        # where copied or moved albums go: the profile's folder, or another for this preview and Apply
        lib = QHBoxLayout()
        lib.setContentsMargins(0, 0, 0, 0)
        self.library_label = QLabel(tr("Library folder"))
        self.library_label.setProperty("role", "hint")
        lib.addWidget(self.library_label)
        self.library_where = _Elided("")
        lib.addWidget(self.library_where, 1)
        self.library_btn = QPushButton(tr("Choose…"))
        self.library_btn.setAccessibleName(tr("Choose another library folder"))
        self.library_btn.clicked.connect(lambda: self.choose_library())
        lib.addWidget(self.library_btn)
        self.library_keep = QLabel(f'<a href="keep">{tr("Make It the Profile’s")}</a>')
        self.library_keep.setTextFormat(Qt.RichText)
        self.library_keep.setAccessibleName(tr("Make this library folder the profile's own"))
        self.library_keep.linkActivated.connect(lambda _h: self.keep_library())
        self.library_keep.hide()
        lib.addWidget(self.library_keep)
        rl.addLayout(lib)
        self.profile.currentTextChanged.connect(self.remember_profile)
        self.profile.currentTextChanged.connect(lambda _t: self.reset_sources())
        self.profile.currentTextChanged.connect(lambda _t: self.reset_library())
        self.profile.currentTextChanged.connect(lambda _t: self.model.refresh_rows() if hasattr(self, "model") else None)
        self.sources.currentIndexChanged.connect(lambda _i: self.show_sources_state())
        # what the preview did without, because a key, login or program is missing (Open Setup: its row)
        self.setup_note = QLabel("")
        self.setup_note.setProperty("role", "hint")
        self.setup_note.setWordWrap(True)
        self.setup_note.setTextFormat(Qt.RichText)
        self.setup_note.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setup_note.linkActivated.connect(lambda link: setup.follow(self.win, link))
        self.setup_note.hide()
        rl.addWidget(self.setup_note)
        # a service busy or not answering while the run asks it (connection.status): a symbol and words
        self.service_note = QLabel("")
        self.service_note.setProperty("role", "attention")
        self.service_note.setWordWrap(True)
        self.service_note.setAccessibleName(tr("Online services"))
        self.service_note.hide()
        rl.addWidget(self.service_note)
        self._service_checked = 0.0
        self.model = PlanModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        # each album's profile (two or more albums listed): a drop-down in its row
        self.model.profile_of = lambda a: (self.profile_of(a), bool(self.own_profile(a)))
        self.model.choose_profile = self.set_album_profile
        self.model.state_of = self.state_of
        self.table.setItemDelegateForColumn(PlanModel.PROFILE, ProfileDelegate(self))
        self.table.setEditTriggers(QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed)
        self.table.clicked.connect(self.profile_clicked)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # (several: apply, leave out, remove)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # (a column widened past the edge)
        self.table.setTextElideMode(Qt.ElideRight)        # (the album's name first, its artist after)
        h = self.table.horizontalHeader()
        left_header(self.table)
        h.setMinimumSectionSize(48)
        # every column can be dragged wider or narrower; each is fitted to its text until you do
        # (Why, the last, takes what is left); a cut cell shows its whole text when pointed at
        for c in range(len(PlanModel.COLS)):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
        h.setStretchLastSection(True)
        self._sized_by_you, self._fitting, self._header_held = set(), False, False
        h.sectionResized.connect(self.column_resized)
        h.viewport().installEventFilter(self)             # (a drag is yours only when it starts on the header)
        self.table.viewport().installEventFilter(self)    # (the list narrower or wider: fitted to the room)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_columns)
        for sig in (self.model.modelReset, self.model.rowsInserted, self.model.dataChanged):
            sig.connect(lambda *_: self._fit_timer.start())
        h.moveSection(PlanModel.PROFILE, 2)               # (an album's profile: next to its name, in view)
        h.moveSection(h.visualIndex(PlanModel.SCANS), h.visualIndex(2) + 1)   # (what the scans said: after Source)
        self.table.setAccessibleName(tr("Albums in the preview"))
        # (while the mouse drags a selection, the inspector waits: reading each album it passes
        # over - tags, covers, on a network share - kept the drag from following the mouse)
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(150)
        self._detail_timer.timeout.connect(self.show_detail_after_drag)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.model.ticked.connect(self.win.update_album_actions)
        self.show_profile_column()
        # the list's own keys, as in a folder: Delete (or Backspace) takes the selected albums out of
        # the list, Escape lets go of the selection. Taken before the menus' shortcuts (Remove Tag is
        # Del too: two shortcuts on one key and Qt runs neither)
        self.table.installEventFilter(self)
        rl.addSpacing(8)                                     # (the controls above, then the list: two groups)
        rl.addWidget(self.table, 1)
        foot = QHBoxLayout()                                 # (what is ready, strong; the other counts, quiet)
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(16)
        self.ready_line = QLabel("")
        self.ready_line.setProperty("role", "headline")
        self.ready_line.hide()
        foot.addWidget(self.ready_line, 0, Qt.AlignTop)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "hint")
        self.summary.setAccessibleDescription(tr("Summary"))    # (no fixed name: the counts are what is read)
        foot.addWidget(self.summary, 1)
        rl.addLayout(foot)
        self.views.addWidget(self.results)
        self.detail = win.inspector.full                  # (the inspector's Evidence > Full preview)
        self.refresh_profiles()
        self.show_library_state()
        self.busy(False)

    def filter(self, text):
        self.proxy.setFilterFixedString(text)

    # page widths under which lower-priority columns give way (the inspector shows them all), so
    # the others fit their text without scrolling sideways: Why first, then Score, the scans and
    # Source (which database said it stays longest)
    NARROW = {5: 1040, 3: 895, 7: 815, 2: 735}            # (each: the narrowest the rest fit in)
    STATUS_MOST = 150                                 # under 600 px the Status column is cut ("Changed sin…",
                                                      # the whole word in its tooltip): the artist stays in view

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for c, w in self.NARROW.items():
            hide = self.width() < w
            if self.table.isColumnHidden(c) != hide:
                self.table.setColumnHidden(c, hide)
                if not hide and hasattr(self, "_fit_timer"):
                    self._fit_timer.start()           # (shown again: fitted to its text)
        self.fit_status()

    COLUMN_MOST = {1: 340, 4: 260, 2: 300, 7: 300}       # (fitted, at most: drag for more)
    # when the fitted columns are wider than the list: these give way first, down to this much
    # (the album folder and the changes are cut and shown whole when pointed at)
    COLUMN_LEAST = ((1, 180), (4, 140), (6, 110), (2, 110), (7, 90))
    LAST_LEAST = 120                                     # (the last column - Why - keeps this much)

    # the list's own keys, as in a folder: Delete or Backspace takes the selected albums out of the
    # list, Escape lets go of the selection
    LIST_KEYS = (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Escape)

    def list_key(self, e) -> bool:
        from PySide6.QtCore import QEvent
        if e.key() not in self.LIST_KEYS or (e.modifiers() & ~Qt.KeypadModifier):
            return False
        if e.key() == Qt.Key_Escape and not self.table.selectionModel().hasSelection():
            return False                                    # (nothing selected: Escape is the window's)
        if e.type() == QEvent.ShortcutOverride:
            e.accept()                                      # (the list's key, before a menu shortcut's)
            return True
        if e.key() == Qt.Key_Escape:
            self.table.clearSelection()
        elif self.running:
            self.win.statusBar().showMessage(tr("Albums can be taken out of the list when the current "
                                                "work ends."), 4000)
        else:
            self.remove_selected()
        return True

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if hasattr(self, "table") and obj is self.table and e.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            if self.list_key(e):
                return True
        if hasattr(self, "_fit_timer") and obj is self.table.viewport() and e.type() == QEvent.Resize:
            self._fit_timer.start()
        elif hasattr(self, "table") and obj is self.table.horizontalHeader().viewport():
            if e.type() == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
                self._header_held = True
            elif e.type() == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton:
                self._header_held = False
        return super().eventFilter(obj, e)

    def last_visible_column(self) -> int:
        h = self.table.horizontalHeader()
        for v in range(h.count() - 1, -1, -1):
            c = h.logicalIndex(v)
            if not self.table.isColumnHidden(c):
                return c
        return -1

    def column_resized(self, c, old, new):
        # (dragged by you, the mouse pressed on the header: left as you made it. Qt's own sizing
        # is not yours - the last column taking what is left, a column hidden or shown while you
        # drag the inspector's edge)
        if self._fitting or not self._header_held or not old or not new or c == self.last_visible_column():
            return
        self._sized_by_you.add(c)

    def fit_columns(self):
        """Each column not sized by you: as wide as its text, up to COLUMN_MOST (Why: the rest)."""
        h = self.table.horizontalHeader()
        last = self.last_visible_column()
        self._fitting = True
        try:
            wants = {}
            for c in range(1, len(PlanModel.COLS)):
                if c in self._sized_by_you or c == last or self.table.isColumnHidden(c):
                    continue
                want = max(self.table.sizeHintForColumn(c), h.sectionSizeHint(c))
                wants[c] = min(want, self.COLUMN_MOST.get(c, want))
            # (all in view where they can be: the widest give way before the list scrolls - while
            # the list is shown, so measured; it is fitted again when its room changes)
            room = self.table.viewport().width() if self.table.isVisible() else 0
            used = sum(wants.get(c, self.table.columnWidth(c)) for c in range(len(PlanModel.COLS))
                       if c != last and not self.table.isColumnHidden(c))
            over = used + self.LAST_LEAST - room if room else 0
            for c, least in self.COLUMN_LEAST:
                if over <= 0:
                    break
                if c in wants and wants[c] > least:
                    give = min(over, wants[c] - least)
                    wants[c] -= give
                    over -= give
            for c, want in wants.items():
                if self.table.columnWidth(c) != want:
                    h.resizeSection(c, want)
        finally:
            self._fitting = False
        self.fit_status()

    def fit_status(self):
        if 0 in getattr(self, "_sized_by_you", ()):
            return
        want = max(self.table.sizeHintForColumn(0), self.table.horizontalHeader().sectionSizeHint(0))
        if self.width() < 600:
            want = min(want, self.STATUS_MOST)
        if self.table.columnWidth(0) != want:
            self._fitting = True
            try:
                self.table.horizontalHeader().resizeSection(0, want)
            finally:
                self._fitting = False

    def context_menu(self, pos):
        keys = ("@release", "match_files", None, "file_info", "tags_from_filenames", None, "process_anyway",
                "exclude_again", None, "play_pause", None, "search_web", "open_folder", "copy_path")
        # (the four ways to choose the release: the Album menu's Change Release submenu, as there)
        release = self.win.menus["Change Release"].menuAction() if "Change Release" in getattr(
            self.win, "menus", {}) else None
        items = [release if k == "@release" else self.win.act(k) if k else None
                 for k in keys if k != "@release" or release is not None]
        chosen = self.selected_albums()
        if chosen:                                     # (what Apply does with the selected rows)
            from PySide6.QtGui import QAction
            from PySide6.QtWidgets import QMenu
            n = len(chosen)
            look = QMenu(tr("Look Up"), self)
            for mode, words in LOOK_UP:
                act = look.addAction(tr(words))
                act.triggered.connect(lambda _=False, m=mode: self.look_up(m))
            look.setEnabled(not self.running or bool(self.looking))     # (while one runs: queued)
            mine = [look.menuAction()]
            again = QAction(tr("Refresh with Current Settings"), self)   # (the profile as it is now)
            again.triggered.connect(self.reload)
            again.setEnabled(not self.running or bool(self.looking))
            mine.append(again)
            # (the profile of the selected albums: theirs, or the one chosen above)
            if self.model.albums:
                pm = QMenu(tr("Profile"), self)
                above = pm.addAction(tr("As Above ({0})").format(self.profile.currentText()))
                above.triggered.connect(lambda _=False, c=chosen: self.set_album_profile(c, ""))
                pm.addSeparator()
                for name in profile_names():
                    act = pm.addAction(name)
                    act.triggered.connect(lambda _=False, c=chosen, n=name: self.set_album_profile(c, n))
                pm.setEnabled(not self.running or (bool(self.looking) and   # (applied: filed again, after)
                                                   any(a.get("status") != "done" for a in chosen)))
                mine.append(pm.menuAction())
            for text, slot, on in ((tr("Apply Selected Albums…") if n > 1 else tr("Apply This Album…"),
                                    self.apply_selected, any(can_apply(a) for a in chosen) and self.may_apply_now()),
                                   (tr("Leave Out of Apply"), lambda: self.leave_out(True),
                                    any(can_apply(a) and not a.get(PlanModel.LEAVE_OUT) for a in chosen)),
                                   (tr("Include in Apply"), lambda: self.leave_out(False),
                                    any(a.get(PlanModel.LEAVE_OUT) for a in chosen)),
                                   (tr("Remove from List"), self.remove_selected, not self.running)):
                act = QAction(text, self)
                act.setEnabled(on)
                act.triggered.connect(lambda _=False, s=slot: s())
                mine.append(act)
            items = items + [None] + mine
        popup_menu(self, self.table, pos, items)

    def selected_albums(self) -> list:
        """The plan's own entries of the selected rows, in the list's order."""
        if not hasattr(self, "table"):
            return []
        rows = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.albums[r] for r in rows if 0 <= r < len(self.model.albums)]

    def leave_out(self, out):
        self.model.set_left_out(self.selected_albums(), out)

    def apply_selected(self):
        """Apply only the selected albums: the others are left out of this Apply (not of the list)."""
        chosen = [a for a in self.selected_albums() if can_apply(a)]
        if not chosen:
            return
        ids = {id(a) for a in chosen}
        self.model.set_left_out([a for a in self.model.albums if id(a) not in ids], True)
        self.model.set_left_out(chosen, False)
        if self.running and self.may_apply_now():
            # (an album the look-up brings in meanwhile is not among them: left out when it ends)
            self._apply_only = {self.album_key(a) for a in chosen}
        self.apply()

    def remove_selected(self):
        """Take the selected albums out of this preview (their folders and files are not touched)."""
        chosen = self.selected_albums()
        if not chosen or self.running:
            return
        ids = {id(a) for a in chosen}
        left = [a for a in self.model.albums if id(a) not in ids]
        if self.plan:
            self.plan["albums"] = left
            self.model.set_plan(self.plan)
        else:                                               # (all applied: the rows shown, no plan behind)
            self.model.set_plan({"albums": left})
        self.removed |= {os.path.realpath(a["source_dir"]) for a in chosen}    # (dropped again: listed again)
        self.summary.setText(tr("{0} taken out of this preview; nothing on disk changed.")
                             .format(plural(len(chosen), tr("album"), tr("albums"))))
        self.win.update_album_actions()

    def refresh_profiles(self):
        cur = self.profile.currentText() or self.starting_profile()
        self._filling = True                          # (filling the list is no choice of yours)
        try:
            self.profile.clear()
            self.profile.addItems(profile_names())
            if cur and self.profile.findText(cur) >= 0:
                self.profile.setCurrentText(cur)
        finally:
            self._filling = False

    @staticmethod
    def starting_profile() -> str:
        """The profile the app starts with (Preferences › default profile): the one last chosen
        ("last"), the built-in one ("builtin"), or a profile named there."""
        try:
            import app_settings
            want = app_settings.get("default_profile") or "last"
            if want == "last":
                return app_settings.get("last_profile") or ""
            return "default" if want == "builtin" else str(want)
        except Exception:
            return ""

    def remember_profile(self, name):
        """The profile you chose: the one the app starts with next time (default_profile last)."""
        if getattr(self, "_filling", False) or not name:
            return
        try:
            import app_settings
            if app_settings.get("last_profile") != name:
                app_settings.set_value("last_profile", name)
        except Exception:
            pass

    def busy(self, on):
        """Preview and Apply are enabled by the window (on this page only); while work runs a
        folder dropped or chosen waits (add_later), and joins the list when the work ends."""
        self.running = on
        self.show_ready()
        if hasattr(self, "model"):
            # (no profile chosen while work runs; during a look-up it is: that album is queued)
            self.model.locked = on and not self.looking
        if not on:
            self._lookup_n = 0                                # (the next run says its own progress)
            self._lookup_i, self._lookup_name = 0, ""
            self._applying = False
            if self.states:                                   # (done: each row says what it is)
                self.states = {}
                self.model.refresh_rows()
        if hasattr(self, "proxy"):
            # (while work runs the rows keep their places: a row is not moved as its state changes)
            self.proxy.setDynamicSortFilter(not on)
        self.choose_btn.setEnabled(not on)
        self.drop.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        self.win.show_progress(on)
        self.win.update_album_actions()
        if not on and getattr(self, "later", None):
            # (after whatever ended has finished: a look-up queued meanwhile goes first)
            QTimer.singleShot(0, self.add_waiting)

    # ---- folders dropped while work runs ---------------------------------------------------
    def add_later(self, paths) -> list:
        """Folders or audio files dropped (or chosen) while work runs: held, and their albums
        added to this list when the work ends; the albums here keep what was found for them.
        Returns the paths taken (not those already in the list or waiting)."""
        from . import manual_match
        if self.worker is not None and getattr(self.worker, "cancelled", False):
            self.win.statusBar().showMessage(tr("Cancelling; drop the folder again when it has stopped."), 5000)
            return []
        new, present = manual_match.new_paths(self, paths)
        held = {os.path.realpath(str(p)) for p in self.later}
        new = [p for p in new if os.path.realpath(str(p)) not in held]
        self.later += new
        if new:
            msg = tr("{0}: added to the list when the current work ends.").format(manual_match._names(new))
        elif present:
            msg = tr("Already in the preview: {0}.").format(manual_match._names(present))
        else:
            msg = tr("Already waiting to be added.")
        self.win.statusBar().showMessage(msg, 6000)
        if new and self.looking:
            self.summary.setText(self.lookup_words(self._lookup_i, self._lookup_name))
        return new

    def add_waiting(self):
        """The work ended: the folders dropped meanwhile join the list under the albums there (each
        read from its own folder: self.roots) and are looked up with the sources chosen above.
        With no list left (all applied), the first is previewed as a drop."""
        if self.running or not self.later:
            return
        from . import manual_match
        paths, self.later = list(self.later), []
        list_only, self._later_list_only = self._later_list_only or _list_first(), False
        new, _ = manual_match.new_paths(self, paths)
        if not new:
            return
        if not self.plan or not self.path:
            manual_match.add_dropped(self.win, new)
            return
        try:
            prof = load_profile(self.profile.currentText())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        folders = list(dict.fromkeys(os.path.abspath(p if os.path.isdir(p) else os.path.dirname(p)) for p in new))

        def work(_w):
            import engine
            out = []                                         # (each folder read on its own: no wider scan)
            for f in folders:
                out += [(f, x) for x in engine.Engine(prof).listing(f, meta=META_STORE).get("albums") or []]
            return out
        self.busy(True)
        self.summary.setText(tr("Adding {0}…").format(manual_match._names(new)))
        self.worker = start(self, work, lambda got: self.added(got, folders, list_only), self.progress, self.oops)

    def added(self, got, folders, list_only):
        """The dropped folders' albums, found: put under the albums already listed, then looked up."""
        self.busy(False)
        if not self.plan:                                    # (the list went meanwhile)
            return
        if self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)
        have = {self.album_key(a) for a in self.plan["albums"]}
        fresh = []
        for root, a in got:
            k = self.album_key(a)
            if k not in have:
                have.add(k)
                self.roots[k] = root
                fresh.append(a)
        extra = len({os.path.realpath(r) for r in self.roots.values()})
        self.where.setText(tr("{0} (and {1} more)").format(self.path, plural(extra, tr("folder"), tr("folders"))))
        if not fresh:
            self.summary.setText(tr("No music found in the folders dropped."))
            return
        self.model.replace_rows([], fresh)
        self.plan["listed"] = True
        self.show_profile_column()
        self.fit_status()
        if list_only:
            self.summary.setText(tr("{0} added, not looked up yet. Select them and press Look Up.").format(
                plural(len(fresh), tr("album"), tr("albums"))))
            self.show_ready()
            return
        self.look_up(self.sources.currentData() or "profile", fresh, queued=True)

    def actionable(self):
        return len(self.to_apply())

    def set_path(self, path, forced=None, within=None) -> bool:
        """Preview a folder; forced: the releases chosen for its albums (engine.Engine.force), as
        the run report's Apply This Release gives one. False when nothing ran."""
        if getattr(self, "running", False):                 # (a second worker would take over Cancel)
            self.win.statusBar().showMessage(tr("Finish or cancel the current work first."), 4000)
            return False
        if not self.may_drop_edits(path):
            return False
        self.path = path
        self.forced = dict(forced or {})
        self.within = list(within or [])                     # (several folders dropped: only these)
        self.looked = {}                                     # (a new folder: nothing looked up yet)
        self.queue = {}                                      # (nor waiting to be)
        self.roots = {}
        self.album_profiles, self.planned_with, self.headers = {}, {}, {}
        self.removed = set()                                 # (albums taken out of this list: their folders)
        self.plan_library = ""
        self.reset_sources()                                 # (a new folder: the profile's sources again)
        self.reset_library()                                 # (and the profile's library folder)
        self.where.setText(path)
        self.views.setCurrentWidget(self.results)
        if self.win.stack.currentWidget() is not self:
            self.win.go("Overview")
        self.win.place_inspector()                          # (something to inspect now)
        self.plan = None
        self.model.set_plan(None)
        self.show_ready()                                    # (the folder before's count: gone)
        self.detail.clear()
        self.busy(False)
        if not self.forced and (_list_first() or self.within):
            self.list_albums()                              # (looked up when you choose: Look Up)
        else:
            self.preview()
        return True

    # ---- List first: the albums shown, looked up when you choose ---------------------------
    def list_albums(self):
        """The folder's albums, not looked up yet: nothing is asked of any database."""
        try:
            prof = load_profile(self.profile.currentText())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        path, within = self.path, list(getattr(self, "within", []) or [])

        def work(_w):
            import engine
            return engine.Engine(prof).listing(path, within=within or None, meta=META_STORE)
        self.busy(True)
        self.summary.setText(tr("Finding the albums… nothing is looked up yet."))
        self.worker = start(self, work, self.listed, self.progress, self.oops)

    def listed(self, plan):
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.model.set_plan(plan)
        self.show_profile_column()
        self.fit_status()
        self.busy(False)
        n = len(plan["albums"])
        self.summary.setText(tr("{0} found, not looked up yet. Select the albums you want and press Look Up "
                                "(none selected: all of them).").format(plural(n, tr("album"), tr("albums"))))
        if self.proxy.rowCount():
            self.table.selectRow(0)

    def look_up_clicked(self):
        """Look Up: the selected albums, or all of them, with the sources chosen beside it."""
        mode = self.sources.currentData() or "profile"
        chosen = self.selected_albums() if self.table.selectionModel().hasSelection() and \
            len(self.selected_albums()) < len((self.plan or {}).get("albums") or []) else None
        if self.plan and chosen:
            self.look_up(mode, chosen)
        elif self.plan:
            self.look_up(mode, list(self.plan["albums"]))
        elif self.path:
            self.preview()

    def reset_sources(self):
        """Back to the profile's own sources: an override is for one look-up only."""
        if hasattr(self, "sources"):
            self.name_profile_sources()
            self.sources.setCurrentIndex(0)
            self.show_sources_state()

    def show_sources_state(self):
        """↺ beside Sources while the choice is not the profile's, and the link that keeps it."""
        if not hasattr(self, "sources"):
            return
        mode = self.sources.currentData() or "profile"
        other = mode != "profile"
        self.sources_label.setText(tr("Sources ↺") if other else tr("Sources"))
        tip = tr("For this look-up only; the profile uses {0}.").format(self.sources_words("profile")) if other else \
            tr("The profile's own sources. Choose another for one look-up; it goes back for the next folder.")
        self.sources_label.setToolTip(tip)
        self.sources.setToolTip(tip)
        self.keep_link.setVisible(other and mode in pf.SOURCES and self.profile.currentText() not in ("", "default"))

    def keep_sources(self):
        """Save the chosen sources as the profile's own (after asking)."""
        mode = self.sources.currentData()
        name = self.profile.currentText()
        if mode not in pf.SOURCES or not name or name == "default":
            return
        if not sheets.question(self, tr("Make {0} the sources of “{1}”?").format(self.sources_words(mode), name),
                               tr("Every look-up with this profile, and its watched folders, then use them."),
                               action=tr("Save to Profile")):
            return
        try:
            data = pf.rename_legacy(pf.raw(name))
            data["source"] = mode
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile was not changed."), str(exc))
            return
        self.reset_sources()
        if "Profiles" in getattr(self.win, "pages", {}):
            self.win.pages["Profiles"].reload()

    # ---- Library folder: where copied or moved albums go, for this preview and Apply ----------
    def covering_source(self, data):
        """The profile's source entry holding the folder previewed (the deepest), or None."""
        if not self.path:
            return None
        real = os.path.realpath(str(self.path))
        best = None
        for s in data.get("sources") or []:
            if not isinstance(s, dict) or not s.get("path"):
                continue
            r = os.path.realpath(os.path.expanduser(s["path"])).rstrip(os.sep)
            if (real == r or real.startswith(r + os.sep)) and (best is None or len(r) > len(best[0])):
                best = (r, s)
        return best[1] if best else None

    def profile_destination(self) -> str:
        """Where the profile files the folder previewed: the destination of the profile's folder
        holding it, if that has its own, else the profile's."""
        try:
            data = load_profile(self.profile.currentText()).data
        except Exception:
            return ""
        src = self.covering_source(data)
        return (src or {}).get("destination") or data.get("destination") or ""

    def in_place(self, name=None) -> bool:
        try:
            return load_profile(name or self.profile.currentText()).data.get("operation") == "in_place"
        except Exception:
            return False

    def planning_profile(self, mode="profile", name=None):
        """The profile Preview, Look Up and (through the plan) Apply use: the Sources choice and
        the library folder chosen here applied to the profile as saved (NAME: an album's own
        profile; none: the one chosen above)."""
        name = name or self.profile.currentText()
        prof = load_profile(name)
        if self.library and not self.in_place(name):
            prof = library_for(prof, self.library)
        return profile_for(prof, mode)

    # ---- a profile per album ----------------------------------------------------------------
    @staticmethod
    def album_key(a) -> str:
        import engine
        return a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or [])

    def profile_of(self, a) -> str:
        """The profile this album is looked up and applied with: its own, or the one above."""
        return self.album_profiles.get(self.album_key(a)) or self.profile.currentText()

    def own_profile(self, a) -> str:
        """The profile chosen in the album's own row ("": the one chosen above)."""
        return self.album_profiles.get(self.album_key(a), "")

    def set_album_profile(self, albums, name):
        """Use profile NAME ("": the one chosen above) for ALBUMS; those already looked up are
        looked up again with it (their destinations and tags are that profile's)."""
        if self.running and not self.looking:              # (during a look-up: looked up after it)
            return
        if name and name not in profile_names():
            return
        # (the versions of one folder are applied together: they share their profile)
        ofs = {(a.get("split") or {}).get("of") for a in albums} - {None, ""}
        albums = list(albums) + [x for x in self.model.albums if (x.get("split") or {}).get("of") in ofs
                                 and not any(x is y for y in albums)]
        again, done = [], []
        for a in albums:
            if a.get("status") == "done":                  # (applied already: filed again, from where it is)
                done.append(a)
                continue
            k = self.album_key(a)
            if name:
                self.album_profiles[k] = name
            else:
                self.album_profiles.pop(k, None)
            if (a.get("status") != "listed" or k in self.looking) and \
                    self.planned_with.get(k) != self.profile_of(a):
                again.append(a)
        self.model.refresh_rows()
        self.show_profile_column()
        if again:
            self.look_up(self.sources.currentData() or "profile", again)
        if done and not self.running:
            self.refile(done, name)
        elif done:
            self.win.statusBar().showMessage(tr("Albums already applied can take another profile when this "
                                                "look-up ends."), 6000)

    def refile(self, done, name):
        """Albums already applied, given another profile (NAME; "": the one chosen above): their
        rows become the albums where Apply put them, looked up there with NAME, so Apply files
        them as NAME does (a job of its own: Undo in Jobs). The tags written name the release
        found, so it is found again."""
        items = []
        for a in done:
            files = [fe["dst"] for fe in a.get("files") or [] if fe.get("dst") and os.path.isfile(fe["dst"])]
            if files:
                items.append((a, os.path.commonpath([os.path.dirname(f) for f in files]), files))
        if not items:
            self.win.statusBar().showMessage(tr("The applied files are no longer where Apply put them."), 6000)
            return
        try:
            prof = load_profile(name or self.profile.currentText())
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return

        def work(_w):
            import engine
            out = []
            for a, folder, files in items:
                want = {os.path.realpath(f) for f in files}
                found = [x for x in engine.Engine(prof).listing(folder, meta=META_STORE).get("albums") or []
                         if want & {os.path.realpath(f) for f in x.get("album_files") or []}]
                out.append((a, folder, found))
            return out
        self.busy(True)
        self.summary.setText(tr("Reading {0} where Apply put them…").format(
            plural(len(items), tr("album"), tr("albums"))))
        self.worker = start(self, work, lambda got: self.refound(got, name), self.progress, self.oops)

    def refound(self, got, name):
        """The applied albums, found where they are now: in their rows' place, then looked up."""
        self.busy(False)
        if not self.plan:                                   # (all applied: the rows shown are the list)
            if not self.model.albums:
                return
            self.plan = {"job": "", "profile": self.profile.currentText(), "operation": "",
                         "albums": self.model.albums, "listed": True, "provider_notes": {}}
        if self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)
        fresh, missing = [], 0
        for a, folder, found in got:
            row = next((i for i, x in enumerate(self.model.albums) if x is a), None)
            if row is None or not found:
                missing += row is not None
                continue
            for x in found:
                k = self.album_key(x)
                self.roots[k] = folder
                if name:
                    self.album_profiles[k] = name
                else:
                    self.album_profiles.pop(k, None)
            self.model.replace_rows([row], found)
            fresh += found
        if missing:
            self.win.statusBar().showMessage(tr("{0} not found where Apply put them.").format(
                plural(missing, tr("album"), tr("albums"))), 6000)
        self.plan["listed"] = any(a.get("status") == "listed" for a in self.model.albums)
        self.show_profile_column()
        self.fit_status()
        if fresh:
            self.look_up(self.sources.currentData() or "profile", fresh, again=True, queued=True)

    def profile_clicked(self, idx):
        """A click on an album's profile opens its drop-down."""
        if idx.isValid() and idx.column() == PlanModel.PROFILE and \
                self.model.flags(self.proxy.mapToSource(idx)) & Qt.ItemIsEditable:
            self.table.edit(idx)

    def show_profile_column(self):
        """The Profile column: always shown, so the list looks the same for one album or many, and
        each album says which profile it is filed with (one album too: chosen in its row or above)."""
        if hasattr(self, "table"):
            self.table.setColumnHidden(PlanModel.PROFILE, False)

    def with_versions(self, albums) -> list:
        """ALBUMS and the other versions of a folder split per version (looked up together)."""
        ofs = {(a.get("split") or {}).get("of") for a in albums} - {None, ""}
        return list(albums) + [x for x in (self.plan or {}).get("albums") or []
                               if (x.get("split") or {}).get("of") in ofs and not any(x is y for y in albums)]

    def plan_for(self, albums) -> dict:
        """The plan of ALBUMS as their profile's preview made it (its job, rules and folders)."""
        name = self.planned_with.get(self.album_key(albums[0])) if albums else None
        head = self.headers.get(name) or self.plan or {}
        # (a list made here - albums applied and filed again - has no job of its own yet)
        out = {"job": "", "profile": self.profile.currentText(), "operation": ""}
        out.update(head)
        out["albums"] = list(albums)
        return out

    def stale_profiles(self, albums) -> list:
        """ALBUMS whose preview was made with another profile than the one now chosen for them."""
        return [a for a in albums if self.planned_with.get(self.album_key(a), self.profile_of(a)) != self.profile_of(a)]

    def reset_library(self):
        """Back to the profile's own library folder: a choice is for one preview only."""
        self.library = ""
        self.show_library_state()

    def show_library_state(self):
        if not hasattr(self, "library_label"):
            return
        name = self.profile.currentText()
        mine = self.profile_destination()
        other = bool(self.library) and self.library != mine
        stays = self.in_place()
        self.library_label.setText(tr("Library folder ↺") if other else tr("Library folder"))
        shown = self.library or mine
        self.library_where.setText(shown or tr("None chosen"))
        if stays:
            tip = tr("This profile does not move files, so it has no library folder.")
        elif other:
            tip = tr("For this preview and Apply only; the profile uses {0}.").format(mine or tr("no folder"))
        else:
            tip = tr("The folder copied or moved albums go to: the profile's own. Choose another for this "
                     "preview and Apply; it goes back for the next folder.")
        for w in (self.library_label, self.library_where, self.library_btn):
            w.setToolTip(tip)
            w.setEnabled(not stays)
        self.library_keep.setVisible(other and not stays and name not in ("", "default"))

    def choose_library(self):
        if self.in_place() or getattr(self, "running", False):
            return
        folder = sheets.choose_folder(self, tr("Library folder"), self.library or self.profile_destination())
        if not folder:
            return
        if not os.path.isdir(folder):
            sheets.warning(self, tr("That folder does not exist."), tr("Library folder kept: {0}").format(
                self.library or self.profile_destination() or tr("none")))
            return
        folder = os.path.abspath(folder)
        if folder == (self.library or self.profile_destination()):
            return
        self.library = "" if folder == self.profile_destination() else folder
        self.show_library_state()
        self.replan_after_library()

    def replan_after_library(self):
        """The albums already looked up were planned for the other folder: look them up again."""
        if not self.plan or not self.path:
            return
        if self.forced:                                    # (a release you chose: kept, so previewed again)
            self.preview()
            return
        again = [a for a in self.plan.get("albums") or [] if a.get("status") not in ("listed", "done")]
        if again:
            self.look_up(self.sources.currentData() or "profile", again, again=True)
            if self.running:
                return
        self.summary.setText(tr("Library folder changed: destinations change at the next Preview or Look Up."))

    def keep_library(self):
        """Save the chosen library folder as the profile's destination (after asking)."""
        name = self.profile.currentText()
        if not self.library or not name or name == "default":
            return
        if not sheets.question(self, tr("Make {0} the library folder of “{1}”?").format(self.library, name),
                               tr("Every copy or move with this profile, and its watched folders, then go there."),
                               action=tr("Save to Profile")):
            return
        try:
            data = pf.rename_legacy(pf.raw(name))
            data["destination"] = self.library
            src = self.covering_source(data)
            if src is not None:                            # (its own folder would win over it)
                src.pop("destination", None)
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The profile was not changed."), str(exc))
            return
        if getattr(self, "plan_library", "") == self.library:
            # (the preview went to this folder, now the profile's own: it still stands for Apply)
            self.plan_library = ""
        self.reset_library()
        if "Profiles" in getattr(self.win, "pages", {}):
            self.win.pages["Profiles"].reload()

    def name_profile_sources(self):
        """The first choice of Look up with says what the profile's sources are."""
        if hasattr(self, "sources"):
            words = self.sources_words("profile")
            self.sources.setItemText(0, words[:1].upper() + words[1:])

    def sources_words(self, mode=None) -> str:
        mode = mode or self.sources.currentData() or "profile"
        if mode == "profile":
            from .profile_editor import SOURCE_WORDS
            try:
                src = load_profile(self.profile.currentText())["source"]
            except Exception:
                src = ""
            return tr("{0} (profile)").format(tr(SOURCE_WORDS.get(src, src)) or "?")
        from .profile_editor import SOURCE_WORDS
        if mode in SOURCE_WORDS:
            return tr(SOURCE_WORDS[mode])
        return {"musicbrainz": tr("MusicBrainz only"), "discogs": tr("Discogs only"),
                "web": tr("the web only (store and encyclopaedia pages)")}.get(mode, mode)

    def lookup_stamp(self, prof, mode, name=None) -> str:
        """What a look-up depends on (the profile as it is now, and the sources): the same again
        finds the same, so it is not asked of the databases twice."""
        import json
        # (the profile's settings themselves: a changed setting is a new look-up)
        import companions
        settings = prof.to_dict() if hasattr(prof, "to_dict") else prof
        # (and the built-in plugins' switches: Discogs or CoverUp switched on or off is a new look-up)
        return json.dumps([name or self.profile.currentText(), mode, self.ocr.isChecked(), settings,
                           companions.discogs_on(), companions.coverup_on()], sort_keys=True, default=str)

    def stale_albums(self) -> list:
        """The albums looked up before their profile (or the sources, or reading scans) changed:
        Refresh looks them up again with the profile as it is now."""
        if not self.plan or not self.looked:
            return []
        mode = self.sources.currentData() or "profile"
        now, out = {}, []
        for a in self.plan.get("albums") or []:
            k = self.album_key(a)
            if k not in self.looked or a.get("status") == "done":
                continue
            name = self.profile_of(a)
            if name not in now:
                try:
                    now[name] = self.lookup_stamp(self.planning_profile(mode, name), mode, name)
                except pf.ProfileError:
                    now[name] = None
            if now[name] is not None and self.looked[k] != now[name]:
                out.append(a)
        return out

    def check_stale(self):
        """Say when albums were looked up with settings since changed, with Refresh for them."""
        if not hasattr(self, "stale_note"):
            return
        n = 0 if self.running else len(self.stale_albums())
        if n:
            self.stale_note.setText(tr("{0} looked up before the profile's settings changed. "
                                       '<a href="refresh">Refresh with the new settings</a>')
                                    .format(tr("1 album was") if n == 1 else tr("{0} albums were").format(n)))
        self.stale_note.setVisible(bool(n))

    def refresh_stale(self):
        """The albums looked up with settings since changed, looked up again with the new ones."""
        stale = self.stale_albums()
        if stale:
            self.look_up(self.sources.currentData() or "profile", stale, again=True)

    def reload(self):
        """Refresh (Ctrl+R): look the selected albums (none selected: all of them) up again, even
        those already looked up the same way (while a look-up runs: queued)."""
        if (self.running and not self.looking) or not self.plan:
            return
        chosen = self.selected_albums() if self.table.selectionModel().hasSelection() else None
        self.look_up(self.sources.currentData() or "profile", chosen or list(self.plan["albums"]), again=True)

    def look_up(self, mode="profile", albums=None, again=False, queued=False):
        """Look up the selected albums (or ALBUMS) with the profile's sources, or only
        MusicBrainz, only Discogs, or only the web; what they find replaces their rows, each
        album's as soon as it is found. An album already looked up the same way is skipped
        unless AGAIN (Refresh). Each album is looked up with its own profile (its row's choice,
        else the one chosen above). Asked while a look-up runs: queued, and looked up when it
        ends (QUEUED: such a look-up, now started; it asks nothing)."""
        import engine
        chosen = albums if albums is not None else self.selected_albums()
        if not chosen or not self.path:
            return
        if self.running:
            if self.looking:                                # (another look-up: this one waits for it)
                self.enqueue(mode, chosen, again)
            return
        if self.plan is not None and self.plan.get("albums") is not self.model.albums:
            self.model.set_plan(self.plan)                  # (the rows are the plan's own: replaced in place)
        groups = {}                                         # profile name -> album keys, in the list's order
        for a in chosen:
            groups.setdefault(self.profile_of(a), {})[self.album_key(a)] = None
        profs, stamps = {}, {}
        for name in groups:
            try:
                profs[name] = self.planning_profile(mode, name)
            except pf.ProfileError as exc:
                sheets.warning(self, tr("This profile cannot be used."), str(exc))
                return
            stamps[name] = self.lookup_stamp(profs[name], mode, name)
        keys = {k for ks in groups.values() for k in ks}
        done = {k for name, ks in groups.items() for k in ks if self.looked.get(k) == stamps[name]} \
            if not again else set()
        if done and done == keys:
            if queued:                                      # (asked when it was queued: nothing to do)
                return
            n = len(keys)
            if not sheets.question(self, tr("{0} already looked up with {1}.").format(
                    tr("This album is") if n == 1 else tr("These {0} albums are").format(n),
                    self.sources_words(mode)),
                    tr("Looking up again asks the databases once more (Refresh, Ctrl+R, does the same)."),
                    action=tr("Look Up Again")):
                return
            done = set()
        groups = {name: [k for k in ks if k not in done] for name, ks in groups.items()}
        groups = {name: ks for name, ks in groups.items() if ks}
        skipped = len(done)
        path, ocr, edits = self.path, self.ocr.isChecked(), self.win.inspector.edits_for_preview()
        forced = dict(self.forced)                           # (the releases you chose: kept, as Preview)
        total = sum(len(ks) for ks in groups.values())

        roots = dict(getattr(self, "roots", {}))

        def work(w):
            off = 0
            for name, ks in groups.items():
                by_root = {}                                # (the folder each album is read from)
                for k in ks:
                    by_root.setdefault(roots.get(k) or path, []).append(k)
                for where, kr in by_root.items():
                    if w.cancelled:
                        break
                    e = engine.Engine(profs[name], ocr=True if ocr else None)
                    e.edits = edits
                    e.force = forced
                    # (each album's rows sent as soon as it is found; cancelled: the others stay as they were)
                    off += plan_each(e, where, kr, lambda k, plan, name=name: w.partial.emit((name, k, plan)),
                                     progress=lambda i, what, o=off: w.progress.emit(o + i, total, what),
                                     cancel=lambda: w.cancelled, meta=META_STORE)
            return off
        self.looking = {k: (name, mode) for name, ks in groups.items() for k in ks}
        self._lookup_again = again                         # (Refresh: so is what Apply interrupts)
        self._lookup_order = [k for ks in groups.values() for k in ks]
        self.states = dict.fromkeys(self._lookup_order, "waiting")
        self.model.refresh_rows()
        self.busy(True)
        self.looked_with = self.sources_words(mode)
        self._lookup_n = total
        self._lookup_skipped = skipped
        self.summary.setText(self.lookup_words(0, ""))
        library = self.library

        def one(part):
            self.plan_library = library
            self.looked_up_one(part[0], part[1], part[2], stamps[part[0]])
        self.worker = start(self, work, lambda _n: self.look_up_ended(), self.progress, self.lookup_failed,
                            partial=one)

    def enqueue(self, mode, albums, again=False):
        """Look Up asked while a look-up runs: the albums wait for it, with these sources (an
        album it is looking up the same way, or already waiting, is not asked twice). After
        Cancel, until the cancelled look-up ends, nothing is queued: Cancel stops it all."""
        if self.worker is not None and getattr(self.worker, "cancelled", False):
            self.win.statusBar().showMessage(tr("Cancelling; Look Up again when it has stopped."), 4000)
            return 0
        n = 0
        for a in albums:
            k = self.album_key(a)
            if self.looking.get(k) == (self.profile_of(a), mode) and k not in self.queue:
                continue
            was = self.queue.pop(k, None)
            self.queue[k] = (mode, again or bool(was and was[1]))
            n += was is None
        if n:
            self.model.refresh_rows()                       # (their rows: Waiting…)
            self.summary.setText(self.lookup_words(self._lookup_i, self._lookup_name))
            self.win.statusBar().showMessage(tr("{0} queued: looked up when this look-up ends.").format(
                plural(n, tr("album"), tr("albums"))), 5000)

    def run_queue(self) -> bool:
        """The look-ups queued while the last one ran: the albums with the same sources at once.
        False when nothing more runs."""
        while self.queue and not self.running:
            mode, again = next(iter(self.queue.values()))
            keys = [k for k, v in self.queue.items() if v == (mode, again)]
            for k in keys:
                self.queue.pop(k)
            albums = [a for a in (self.plan or {}).get("albums") or [] if self.album_key(a) in keys]
            seen, chosen = set(), []
            for a in albums:                                 # (an album planned as several rows: once)
                if self.album_key(a) not in seen:
                    seen.add(self.album_key(a))
                    chosen.append(a)
            if chosen:
                self.look_up(mode, chosen, again, queued=True)
        return self.running

    def lookup_words(self, i, name) -> str:
        """The line under the list while albums are looked up: which one of how many."""
        n = self._lookup_n
        head = tr("Looking up album {0} of {n} with {w}").format(i + 1, n=n, w=self.looked_with) if name else \
            tr("Looking up {0} with {1}").format(plural(n, tr("album"), tr("albums")), self.looked_with)
        tail = tr(": {0}").format(name) if name else ""
        skipped = getattr(self, "_lookup_skipped", 0)
        note = tr(" ({0} already looked up, skipped)").format(skipped) if skipped else ""
        queued = len(getattr(self, "queue", ()))
        then = tr(" Then {0} queued.").format(plural(queued, tr("album"), tr("albums"))) if queued else ""
        later = len(getattr(self, "later", ()))
        if later:
            then += tr(" Then the {0} dropped.").format(plural(later, tr("folder"), tr("folders")))
        return head + tail + "…" + note + then + tr(" Nothing is changed while previewing.")

    def remember_looked(self, albums, stamp):
        """The albums found, as looked up with STAMP (cancelled: only those it reached); not one
        that failed or a database was busy or not answering for: that one is worth asking again."""
        import engine
        if stamp is None:
            return
        import reasons
        for a in albums:
            # (a plan entry keeps them in its identification: the reason with its codes in front)
            ident = a.get("identification") or {}
            codes = set(a.get("reason_codes") or ident.get("reason_codes") or [])
            words = reasons.parse(ident["reason"])[1] if ident.get("reason") else a.get("reason", "")
            if a.get("status") == "listed" or codes & RETRY_CODES or str(words).startswith("error:") or \
                    str(a.get("reason", "")).startswith("error:"):
                continue
            self.looked[a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or [])] = stamp

    def looked_up_one(self, name, key, new, stamp=None):
        """Album KEY looked up with profile NAME (NEW: its plan, its rows): they take the
        places of the album's rows at once; the other rows stay as they were."""
        self.headers[name] = {k: v for k, v in new.items() if k != "albums"}
        self.states.pop(key, None)
        found = list(new["albums"])
        keys = {key} | {self.album_key(a) for a in found}
        for a in found:
            self.planned_with[self.album_key(a)] = name
        self.remember_looked(found, stamp)
        if self.plan is None:
            self.plan = dict(self.headers[name], albums=self.model.albums)
        head = self.headers.get(self.profile.currentText()) or self.headers[name]
        self.plan.update(head, albums=self.model.albums)
        was = self.selected()
        rows = [r for r, a in enumerate(self.model.albums) if self.album_key(a) in keys]
        out = {self.album_key(self.model.albums[r]) for r in rows if self.model.albums[r].get(PlanModel.LEAVE_OUT)}
        for a in found:
            if self.album_key(a) in out:
                a[PlanModel.LEAVE_OUT] = True
        mine = was is not None and any(was is self.model.albums[r] for r in rows)
        at = self.model.replace_rows(rows, found)
        self.plan["listed"] = any(a["status"] == "listed" for a in self.model.albums)
        if mine:                                          # (the album shown: shown as found now)
            if found and not self.table.selectionModel().hasSelection():
                self.table.selectRow(self.proxy.mapFromSource(self.model.index(at, 0)).row())
            self.show_detail()
        self.win.update_album_actions()

    def look_up_ended(self):
        """The look-up ended (done or cancelled): the counts, then the look-ups queued meanwhile
        (stopped for Apply: the albums it did not reach wait, and Apply asks first)."""
        apply_next, self._apply_next = getattr(self, "_apply_next", False), False
        only, self._apply_only = getattr(self, "_apply_only", None), None
        if apply_next:
            left = [k for k in getattr(self, "_lookup_order", []) if k in self.states and k in self.looking]
            again = bool(getattr(self, "_lookup_again", False))
            ahead = dict(self.queue)
            self.queue = {k: (self.looking[k][1], again) for k in left}
            for k, (mode, ag) in ahead.items():          # (asked meanwhile: theirs wins, Refresh kept)
                was = self.queue.pop(k, None)
                self.queue[k] = (mode, ag or bool(was and was[1]))
        self.looking = {}
        self.show_profile_column()
        self.fit_status()
        self.check_stale()
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.show_counts()
        self.refresh_overrides()
        self.busy(False)
        self.service_status(force=True)
        if apply_next:
            self.model.refresh_rows()                     # (the albums not reached: Waiting…)
            if only is not None:                           # (Apply Selected: only the albums chosen)
                self.model.set_left_out([a for a in self.model.albums if self.album_key(a) not in only], True)
            if self.actionable():
                self.apply()
            if not self.running:                           # (Apply not asked after all: on with the look-up)
                self.run_queue()
            return
        if self.run_queue():                               # (the next one says what it does)
            return
        notify(self.win, "finished", tr("Preview finished"), counts_by_label((self.plan or {}).get("albums") or []))
        if self.proxy.rowCount() and not self.table.selectionModel().hasSelection():
            self.table.selectRow(0)
        self.win.refresh_attention_badge(self.path)

    def lookup_failed(self, msg):
        self._apply_next, self._apply_only = False, None
        self.looking, self.queue = {}, {}                  # (nothing queued starts after a failure)
        self._later_list_only = bool(self.later)           # (folders dropped meanwhile: listed, not looked up)
        self.oops(msg)

    def preview_release(self, folder, ref, files=None, only=False) -> bool:
        """Preview the album in `folder` with the release `ref` (a MusicBrainz ID or
        "discogs:123"); files: the album's own files, for one album of a folder holding several;
        only: those files alone (one version of a folder holding two), the rest left out.
        Only the preview: nothing is written until Apply, which asks first."""
        import engine
        if not folder or engine.parse_release_ref(ref)[0] is None:
            return False
        key = os.path.realpath(str(folder))
        choice = [dict({"ref": ref, "files": list(files)}, **({"only": True} if only else {}))] if files else ref
        mine = {os.path.realpath(str(f)) for f in files or ()}
        listed = [a for a in (self.plan or {}).get("albums") or []
                  if os.path.realpath(a["source_dir"]) == key
                  and (not mine or mine & {os.path.realpath(f) for f in engine.plan_entry_files(a)})]
        if listed and self.path:
            # (the album is in the list already: it alone is looked up again, the list stays)
            self.forced[key] = choice
            if self.win.stack.currentWidget() is not self:
                self.win.go("Overview")
            self.look_up(self.sources.currentData() or "profile", self.with_versions(listed), again=True)
        elif not self.set_path(str(folder), {key: choice}):
            return False
        self.win.statusBar().showMessage(tr("Previewing {name} with the release you chose; nothing is written "
                                            "until you apply.").format(name=Path(str(folder)).name), 10000)
        return True

    def may_drop_edits(self, path) -> bool:
        """Another folder is previewed: your edits of this one are dropped, after asking (they
        are of the preview only; nothing was written). The same folder again keeps them."""
        insp = self.win.inspector
        same = bool(self.path) and os.path.realpath(str(path)) == os.path.realpath(str(self.path))
        if same:
            return True
        if insp.edits:
            n = len(insp.edits)
            where = Path(str(self.path)).name or str(self.path) if self.path else tr("this preview")
            if not sheets.confirm(self, tr("Discard your edits of {0}?").format(plural(n, tr("album"), tr("albums"))),
                                  tr("Your tag and cover edits of {where} are not kept when you preview another "
                                     "folder; nothing was written.").format(where=where),
                                  tr("Cancel keeps them: apply this preview first to write them."), tr("Discard Edits"),
                                  destructive=True):
                return False
        insp.forget_edits()                                 # (your edits were of the other folder)
        return True

    def service_status(self, force=False):
        """Say under the folder when MusicBrainz, Discogs, AcoustID or the Cover Art Archive is
        busy or not answering this run's requests (asked at most every 10 seconds)."""
        import time
        import connection
        if not force and time.monotonic() - self._service_checked < 10:
            return
        self._service_checked = time.monotonic()
        st = connection.status(ex.STATE_DIR / "cache.sqlite")
        lines = []
        for svc, how in sorted(st.items()):
            name = connection.TITLES.get(svc, svc)
            if how == "busy":
                lines.append(tr("{0} is busy: TagMender waits and tries again, so this takes longer.").format(name))
            elif svc == "coverartarchive":            # (it proves no album: only its covers wait)
                lines.append(tr("{0} is not answering: covers from it are skipped for now.").format(name))
            elif svc == "acoustid":
                lines.append(tr("{0} is not answering: fingerprints are not looked up for now.").format(name))
            else:
                lines.append(tr("{0} is not answering: albums only it can prove are left to try again later.")
                             .format(name))
        if lines:                                     # (a symbol with the words, never colour alone)
            self.service_note.setText("⚠ " + "  ".join(lines))
        self.service_note.setVisible(bool(lines))
        self.service_note.setAccessibleDescription(" ".join(lines))
        badge = getattr(self.win, "service_badge", None)
        if badge is not None:                         # (the same, short, on every page)
            short = [tr("{0} busy").format(connection.TITLES.get(s, s)) if how == "busy" else
                     tr("{0} not answering").format(connection.TITLES.get(s, s)) for s, how in sorted(st.items())]
            badge.setText("⚠ " + ", ".join(short) if short else "")
            badge.setToolTip(" ".join(lines))
            badge.setAccessibleDescription(" ".join(lines))
            badge.setVisible(bool(short))

    def progress(self, i, n, what):
        self.service_status()
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i + 1)
        self.win.show_count(tr("{0} of {n}").format(i + 1, n=n))     # (beside the bar: it is too thin for words)
        words = tr("{0} of {n}: {name}").format(i + 1, n=n, name=Path(what).name)
        self.bar.setToolTip(words)
        self.bar.setAccessibleDescription(words)
        if self._applying and self.running:                 # (Apply: which album; file_progress: which file)
            self.applying_row(i)
            self._apply_at = (i, n, Path(what).name)
            self.summary.setText(tr("Applying album {0} of {n}: {name}…").format(i + 1, n=n, name=Path(what).name))
        elif getattr(self, "_lookup_n", 0) and self.running:  # (Look Up: which album, under the list)
            self._lookup_i, self._lookup_name = i, Path(what).name
            if i < len(self._lookup_order) and self.states.get(self._lookup_order[i]) == "waiting":
                self.states[self._lookup_order[i]] = "looking"
                self.model.refresh_rows()
            self.summary.setText(self.lookup_words(i, Path(what).name))
        elif self.running:
            self.summary.setText(tr("Identifying album {0} of {n}: {name}… nothing is changed while previewing.")
                                 .format(i + 1, n=n, name=Path(what).name))

    def file_progress(self, i, j, n, path):
        """Apply: file J (0-based) of the N its album I has to write or move, next."""
        if not (self._applying and self.running):
            return
        self.applying_row(i)
        _i, total, name = getattr(self, "_apply_at", (i, 0, ""))
        words = tr("Applying album {0} of {n}: {name} — file {1} of {m}: {file}").format(
            i + 1, j + 1, n=total, name=name, m=n, file=Path(path).name)
        self.summary.setText(words)
        self.bar.setToolTip(words)
        self.bar.setAccessibleDescription(words)

    def applying_row(self, i):
        """Apply's album I (of the rows it was given) is the one being applied now."""
        rows = getattr(self, "_applied_rows", None) or []
        if not (0 <= i < len(rows) and self.plan and 0 <= rows[i] < len(self.plan["albums"])):
            return
        me = id(self.plan["albums"][rows[i]])
        if self.states.get(me) != "applying":
            self.states = {k: v for k, v in self.states.items() if v != "applying"}   # (the one before: done)
            self.states[me] = "applying"
            self.model.refresh_rows()

    def state_of(self, a) -> str:
        """What is being done with album A now (its Status column), or ""."""
        if not self.states and not self.queue:
            return ""
        k = self.states.get(id(a))
        if k is None:
            key = self.album_key(a)
            k = self.states.get(key) or ("waiting" if key in self.queue else None)
        return tr(ROW_STATES[k]) if k else ""

    def set_folders(self, folders) -> bool:
        """Several folders dropped at once: every album in them listed together, under the folder
        that holds them all; nothing is looked up until you choose Look Up or Preview."""
        folders = [os.path.abspath(str(f)) for f in folders]
        root = os.path.commonpath(folders) if len(folders) > 1 else folders[0]
        if root in folders and len(folders) > 1:            # (one folder inside another: its parent)
            root = os.path.dirname(root)
        if not self.set_path(root, within=folders):
            return False
        self.where.setText(tr("{0} ({1} folders)").format(root, len(folders)))
        return True

    def preview(self):
        if not self.path:
            return
        if (getattr(self, "within", None) or self.album_profiles or getattr(self, "roots", None)) and self.plan:
            # (several folders: just the albums listed; albums with a profile of their own: each with it)
            self.look_up(self.sources.currentData() or "profile", list(self.plan["albums"]))
            return
        mode = self.sources.currentData() or "profile"
        try:
            prof = self.planning_profile(mode)   # (Look up with: its choice)
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        self.looked_with = self.sources_words(mode)
        path, ocr, forced = self.path, self.ocr.isChecked(), dict(self.forced)
        edits = self.win.inspector.edits_for_preview()     # (your edits from the inspector: kept)

        def work(w):
            import engine
            e = engine.Engine(prof, ocr=True if ocr else None)    # unticked: the profile's "ocr"
            e.force = forced
            e.edits = edits
            return e.plan(path, progress=w.progress.emit, cancel=lambda: w.cancelled, meta=META_STORE)
        self.busy(True)
        self.summary.setText(tr("Identifying with {0}… nothing is changed while previewing.").format(self.looked_with))
        stamp = None if forced else self.lookup_stamp(prof, mode)    # (a release you chose: not the same)

        library = self.library
        name = self.profile.currentText()

        def done(plan):
            self.looked = {}
            self.album_profiles = {}                         # (every album previewed afresh: with the one above)
            self.headers = {name: {k: v for k, v in plan.items() if k != "albums"}}
            self.planned_with = {self.album_key(a): name for a in plan.get("albums") or []}
            self.plan_library = library
            self.remember_looked(plan.get("albums") or [], stamp)
            self.planned(plan)
        self.worker = start(self, work, done, self.progress, self.oops)

    def planned(self, plan):
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.model.set_plan(plan)
        self.show_profile_column()
        self.fit_status()
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.show_counts()
        self.refresh_overrides()
        self.busy(False)
        self.service_status(force=True)
        notify(self.win, "finished", tr("Preview finished"), counts_by_label(plan["albums"]))
        if self.proxy.rowCount():
            self.table.selectRow(0)
        self.win.refresh_attention_badge(self.path)

    def show_counts(self):
        plan = self.plan or {"albums": []}
        notes = "; ".join(setup.other_notes(plan))
        said = setup.plan_notices(plan)
        self.setup_note.setText("<br>".join(setup.rich(t, i) for i, t in said))
        self.setup_note.setAccessibleName(" ".join(t for _i, t in said))
        self.setup_note.setVisible(bool(said))
        # (counted as the rows name them: an album shown "Ignored" is not "Excluded" here)
        said_with = getattr(self, "looked_with", "")
        self.summary.setText(counts_by_label(plan["albums"]) + (f"   ({notes})" if notes else "") +
                             (tr(" Looked up with {0}.").format(said_with) if said_with else ""))
        self.show_ready()

    def show_ready(self):
        """The bold line under the list: how many albums Apply would carry out now (the ticked
        ones, or of two or more highlighted rows those), nothing while work runs or no list shows."""
        if not hasattr(self, "ready_line"):
            return
        albums = (self.plan or {}).get("albums") or []
        if not albums or self.running:
            self.ready_line.hide()
            return
        n = len(self.to_apply())
        self.ready_line.setText(tr("1 album ready to apply") if n == 1 else
                                tr("{0} albums ready to apply").format(n) if n else tr("Nothing to apply yet"))
        self.ready_line.show()

    def edited(self, album):
        """Your edit in the inspector changed an album of the preview: its row, the counts and Apply."""
        if not self.plan or self.running or not any(a is album for a in self.plan["albums"]):
            return
        for r, a in enumerate(self.model.albums):
            if a is album:
                self.model.dataChanged.emit(self.model.index(r, 0),
                                            self.model.index(r, self.model.columnCount() - 1))
        self.show_counts()
        self.win.update_album_actions()

    def oops(self, msg):
        self.busy(False)
        self.summary.setText(tr("Stopped: {head}").format(head=(msg.splitlines() or [""])[0]))
        head, _, rest = msg.partition("\n")
        sheets.warning(self, tr("Stopped: {head}").format(head=head), rest.strip())

    def to_apply(self):
        """The albums Apply carries out: those with changes, unless left out (unticked); with
        two or more rows highlighted, only those of them (the others stay as they are)."""
        albums = [a for a in (self.plan or {}).get("albums", [])
                  if can_apply(a) and not a.get(PlanModel.LEAVE_OUT)]
        chosen = self.selected_albums() if hasattr(self, "table") else []
        if len(chosen) >= 2:
            ids = {id(a) for a in chosen}
            albums = [a for a in albums if id(a) in ids]
        return albums

    def apply_words(self):
        """The Apply sheet: (headline, what changes, how to undo, button), from each album's own
        operations - an album that stays in its folder is not said to be moved."""
        from .inspector import where_files_go
        albums = self.to_apply()
        n = len(albums)
        ops = {fe["op"] for a in albums for fe in a.get("files") or [] if fe.get("op") != "none"}
        moved = {"move": tr("moved"), "copy": tr("copied")}
        how = tr(" and ").join(moved[o] for o in ("move", "copy") if o in ops)
        def outcome(a):
            w = where_files_go(a) or tr("Stays in its folder, tags only")
            return w[:1].lower() + w[1:]
        names = {self.profile_of(a) for a in albums}
        if len(names) > 1:                            # (several profiles: each album says its own)
            plain = outcome

            def outcome(a):
                return tr("{0} (profile {1})").format(plain(a), self.profile_of(a))
        outcomes = {outcome(a) for a in albums}
        stays = {w[:1].lower() + w[1:] for w in (tr("Stays in its folder, names unchanged"),
                                                  tr("Stays in its folder, tags only"))}
        if outcomes <= stays:                         # (the sentence above says it: the albums by name)
            what = tr("Their tags are written; each album stays in its own folder:")
            items = [html.escape(album_words(a["source_dir"])) for a in albums[:APPLY_LISTED]]
        else:
            what = tr("Their tags are written and their files {how} as listed:").format(how=how) if how else \
                tr("Their tags are written; each album stays in its own folder, as listed:")
            items = [f"{html.escape(album_words(a['source_dir']))}: {html.escape(outcome(a))}"
                     for a in albums[:APPLY_LISTED]]
        if n > APPLY_LISTED:
            items.append(html.escape(tr("and {0} more").format(n - APPLY_LISTED)))
        # (rich text: a wrapped line hangs under its words, not under the bullet)
        body = (f"<p>{html.escape(what)}</p><ul style='margin-left:-24px; margin-top:0'>"
                + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        mine = sum(1 for a in albums if a.get("edited") and not planmod.edit_empty(a["edited"]))
        if mine:                                      # (values you typed or a cover you chose, not the match's)
            body += tr("<p>Includes your own edits of {0}: tags or a cover you chose, marked with a pencil in the "
                       "inspector.</p>").format(plural(mine, tr("album"), tr("albums")))
        word = tr("Album") if n == 1 else tr("Albums")
        return (tr("Apply the preview to {n} {0}?").format(word.lower(), n=n), body,
                tr("Every change can be undone: Edit > Undo Last Job, or Jobs."), tr("Apply {n} {0}").format(word, n=n))

    def may_apply_now(self) -> bool:
        """Apply can be asked: nothing runs, or a look-up does (it stops after its album first)."""
        if not self.running:
            return True
        w = self.worker
        return bool(self.looking) and not getattr(self, "_apply_next", False) and \
            not (w is not None and getattr(w, "cancelled", False))

    def apply_during_look_up(self) -> bool:
        """Apply asked while albums are looked up: the look-up stops after the album it is on;
        the albums not reached wait (queued), Apply asks as usual, then they are looked up."""
        if not self.may_apply_now() or not self.running:
            return False
        self._apply_next = True
        self.worker.cancelled = True
        self.summary.setText(tr("Stopping the look-up after the current album to apply; the albums not yet "
                                "looked up are looked up afterwards."))
        self.win.update_album_actions()
        return True

    def apply(self):
        n = self.actionable()
        if not n or self.edit_pending():
            return
        if self.running:
            self.apply_during_look_up()
            return
        if getattr(self, "plan_library", "") != (self.library or ""):
            # (the preview was made for another library folder: its files would go there)
            sheets.information(self, tr("Preview again first."),
                               tr("The library folder changed since this preview; its files would go to the "
                                  "folder it was made for. Preview or Look Up again, then apply."))
            return
        stale = self.stale_profiles(self.to_apply())
        if stale:
            # (previewed with another profile than the one now chosen for them)
            sheets.information(self, tr("Preview again first."),
                               tr("The profile of {0} changed since this preview. Preview or Look Up again, "
                                  "then apply.").format(plural(len(stale), tr("album"), tr("albums"))))
            return
        # always asked here: the profile's "Apply without asking" is for its watched folders only
        if not sheets.confirm(self, *self.apply_words()) or self.edit_pending():
            return
        chosen = self.to_apply()
        self.release_files()
        # (only the ticked albums go to the executor, one job per profile they were previewed
        # with; what it reports is mapped back to the list)
        rows = [i for i, a in enumerate(self.plan["albums"]) if any(a is c for c in chosen)]
        groups = {}
        for i in rows:
            a = self.plan["albums"][i]
            groups.setdefault(self.planned_with.get(self.album_key(a)), []).append(i)
        self._applied_rows = [i for g in groups.values() for i in g]
        plans = []
        for g in groups.values():
            plan = self.plan_for([self.plan["albums"][i] for i in g])
            plan["job"] = self.fresh_job(plan.get("job") or "")
            plans.append(plan)

        offs, total = [], 0                               # (album i of all the plans: counted on)
        for p in plans:
            offs.append(total)
            total += len(p["albums"])

        def work(w):
            import executor
            at = iter(offs)

            def run(p):
                o = next(at)
                return executor.Executor(p, cancel=lambda: w.cancelled,
                                         progress=lambda i, _n, what: w.progress.emit(o + i, total, what),
                                         file_progress=lambda i, j, n, f: w.partial.emit((o + i, j, n, f))).run()
            return merge_results(plans, run, lambda: w.cancelled)
        self.states = {id(self.plan["albums"][i]): "waiting" for i in self._applied_rows}
        self.model.refresh_rows()
        self.busy(True)
        self._applying, self._apply_at = True, (0, total, "")
        self.summary.setText(tr("Applying…"))
        self.worker = start(self, work, self.applied, self.progress, self.apply_failed,
                            partial=lambda x: self.file_progress(*x))

    def apply_failed(self, msg):
        """Apply stopped with an error: said as any failure; the albums a look-up was
        interrupted for (Apply during a look-up) are looked up still."""
        self.oops(msg)
        if self.queue and not self.running:
            self.run_queue()

    def fresh_job(self, job) -> str:
        """JOB, or (Apply ran it already: albums left out of it applied now) a new job named
        after it, which keeps the artwork the preview staged under that name."""
        import executor
        base, n = job, 1
        while not job or job in self.used_jobs or (executor.job_dir(job) / "journal.jsonl").exists():
            n += 1
            job = f"{base}-{n}" if base else time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.used_jobs.add(job)
        return job

    def release_files(self):
        """The player lets go of every file Apply is about to write, move or copy (a track playing
        from the album, or one queued after it, would hold the old file open)."""
        player = getattr(self.win, "player", None)
        if player is None:
            return False
        paths = [p for a in self.to_apply() for fe in a.get("files") or []
                 for p in (fe.get("src"), fe.get("dst")) if p]
        return player.release(paths)

    def edit_pending(self):
        """An edit of yours is still on its way to the preview: Apply would leave it out."""
        if not self.win.inspector.pending():
            return False
        self.win.statusBar().showMessage(tr("Your edit is still being applied to the preview. "
                                            "Apply again in a moment."), 5000)
        return True

    def applied(self, res):
        rows = getattr(self, "_applied_rows", None)
        if not isinstance(rows, list) or not rows:          # (the whole plan was given)
            rows = list(range(len(self.plan["albums"])))
        done_albums = [self.plan["albums"][i] for i in rows]
        names = [n for n in dict.fromkeys((getattr(self, "planned_with", None) or {}).get(Overview.album_key(x))
                                          for x in done_albums) if isinstance(n, str) and n]
        rep = run_report(dict(self.plan, albums=done_albums, **({"profile": ", ".join(names)} if names else {})), res)
        # (the executor's index is into the albums it was given: the list's own row here)
        by_index = {rows[r["index"]]: r for r in res["albums"] if "index" in r and r["index"] < len(rows)}
        by = {a["album"]: a for a in res["albums"]}
        for i, a in enumerate(self.plan["albums"]):
            r = by_index.get(i) if by_index else by.get(a["source_dir"])
            if r:
                a["status"] = {"done": "done"}.get(r["status"], r["status"])
                if r["status"] in ("failed", "conflict"):
                    a["conflicts"] = [r["detail"]]
                if r.get("label") in ("Ignored", "Skipped"):
                    a["label"] = r["label"]
        self.model.set_plan(self.plan)
        many = ", " in (res.get("job") or "")
        self.summary.setText((tr("Jobs {0}: ").format(res["job"]) if many else f"Job {res['job']}: " if res.get("job")
                              else "") + (counts_text(res["counts"]) or tr("Nothing was changed")) +
                             ((tr(". Undo them from Jobs.") if many else tr(". Undo it from Jobs."))
                              if res.get("job") and set(res["counts"]) - NOTHING_DONE
                              else "."))           # (no job, or nothing changed by it: nothing to undo)
        left = [a for a in self.plan["albums"] if can_apply(a)]
        if left:                                          # (albums left out stay, to apply later)
            self.summary.setText(self.summary.text() + " " + tr("{0} left to apply.").format(
                plural(len(left), tr("album"), tr("albums"))))
        elif not self.queue:
            self.plan = None
            self.show_ready()
        self.busy(False)
        self.service_status(force=True)
        notify(self.win, "finished", tr("Apply finished"), counts_text(res["counts"]))
        self.win.pages["Jobs"].reload()
        self.win.refresh_attention_badge(self.path)          # (Apply marks albums Unresolved on disk)
        if rep is not None:
            self.win.show_run_report(rep)                  # (what was done, what is left for you)
        if self.queue:                                     # (stopped for this Apply: on with the look-up)
            self.run_queue()

    def cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self._apply_next, self._apply_only = False, None   # (Cancel stops it all, Apply too)
            n = len(self.queue)
            self.queue = {}                                # (nor is what waited for it looked up)
            if self.later:                                 # (nor are the folders dropped meanwhile added)
                from .manual_match import _names
                self.win.statusBar().showMessage(tr("Not added: {0}.").format(_names(self.later)), 6000)
                self.later = []
            self.states = {k: v for k, v in self.states.items() if v != "waiting"}
            self.model.refresh_rows()
            self.summary.setText(tr("Cancelling after the current album…") + (tr(
                " The {0} queued are not looked up.").format(plural(n, tr("album"), tr("albums"))) if n else ""))

    def selected(self):
        idx = self.table.currentIndex() if hasattr(self, "table") and self.table.selectionModel().hasSelection() \
            else None
        if not idx or not idx.isValid():
            return None
        # the plan's own entry, not proxy.data(UserRole): Qt hands a dict back as a copy, and an
        # edit (the inspector, Tags from File Names) must change the album the plan applies
        row = self.proxy.mapToSource(self.proxy.index(idx.row(), 0)).row()
        return self.model.albums[row] if 0 <= row < len(self.model.albums) else None

    def selection_changed(self, *_):
        from PySide6.QtWidgets import QApplication
        if QApplication.mouseButtons() & Qt.LeftButton:
            self._detail_timer.start()
        else:
            self._detail_timer.stop()
            self.show_detail()

    def show_detail_after_drag(self):
        from PySide6.QtWidgets import QApplication
        if QApplication.mouseButtons() & Qt.LeftButton:     # (still dragging)
            self._detail_timer.start()
        else:
            self.show_detail()

    def show_detail(self, *_):
        a = self.selected()
        if a is None:
            self.detail.clear()
            self.win.inspector.clear()
        else:
            one = dict(self.plan_for([a]), summary={a["status"]: 1})
            self.detail.setPlainText(planmod.preview(one, verbose=False))
            self.win.inspector.show_album(a, self.plan)
        self.busy(bool(getattr(self, "running", False)))

    def choose_release(self):
        a = self.selected()
        if not a:
            return
        import engine
        key = os.path.realpath(a["source_dir"])
        files = engine.plan_entry_files(a)
        mine = {os.path.realpath(f) for f in files}
        choices = [c for c in (self.forced.get(key) or []) if isinstance(c, dict)]
        was = next((c for c in choices if mine & {os.path.realpath(f) for f in c.get("files") or []}), None)
        ref, ok = sheets.get_text(self, tr("Use another release for {name}").format(name=Path(a['source_dir']).name),
                                  tr("Paste a MusicBrainz or Discogs release link, or its ID. The album is "
                                     "previewed again with it; leave it empty to let TagMender choose."),
                                  text=(was or {}).get("ref", ""), action=tr("Use Release"),
                                  placeholder="https://musicbrainz.org/release/…")
        if not ok:
            return
        ref = ref.strip()
        if ref and engine.parse_release_ref(ref)[0] is None:
            sheets.warning(self, tr("That is not a MusicBrainz or Discogs release link."),
                           tr("Copy the address of the release page (musicbrainz.org/release/… or "
                              "discogs.com/release/…), or just its ID."))
            return
        # (this album only: a folder may hold several, each with its own choice)
        choices = [c for c in choices if c is not was]
        if ref:
            choices.append(dict({"ref": ref, "files": files}, **({"only": True} if a.get("split") else {})))
        if choices:
            self.forced[key] = choices
        else:
            self.forced.pop(key, None)
        # (this album only - not every album listed - looked up again with the release chosen)
        self.look_up(self.sources.currentData() or "profile", self.with_versions([a]), again=True)

    def process_anyway(self):
        a = self.selected()
        if a and a["status"] == "excluded":
            folder = Path(a["source_dir"])
            mine = {fe["src"] for fe in a.get("files") or []} | {s.get("file") for s in a.get("skipped") or []} \
                | set(a.get("excluded_files") or [])
            mine.discard(None)
            # only the album's own files, never its folder: loose tracks have a watched folder or
            # a library root for theirs, and the rules would be off for all dropped there later
            if mine:
                for f in sorted(mine):
                    ex.State().allow(f)
                what = Path(sorted(mine)[0]).name if len(mine) == 1 else \
                    tr("{0} files in {name}").format(len(mine), name=folder.name)
            else:
                ex.State().allow(folder)
                what = folder.name
            self.summary.setText(tr("Rules will not exclude {what}. Preview again to include it; to exclude it "
                                    "again, choose Album > Exclude Again.").format(what=what))
            self.refresh_overrides()

    # ---- Process Anyway, undone ------------------------------------------------
    def refresh_overrides(self):
        """The paths Process Anyway let through (read once per preview, not per selection)."""
        try:
            self.overrides = {os.path.abspath(str(p)) for p in ex.State().overrides()}
        except Exception:
            self.overrides = set()
        self.win.update_album_actions()

    def _album_paths(self, a):
        folder = a["source_dir"]
        mine = {fe["src"] for fe in a.get("files") or []} | {s.get("file") for s in a.get("skipped") or []} \
            | set(a.get("excluded_files") or []) | {folder}
        mine.discard(None)
        return {os.path.abspath(str(p)) for p in mine}

    def overridden(self, a=None):
        """The selected album's paths that Process Anyway let through (none: Exclude Again is off)."""
        a = a or self.selected()
        return sorted(self._album_paths(a) & getattr(self, "overrides", set())) if a else []

    def exclude_again(self):
        a = self.selected()
        paths = self.overridden(a)
        if not paths:
            return
        st = ex.State()
        for p in paths:
            st.disallow(p)
        self.refresh_overrides()
        self.summary.setText(tr("The rules apply to {name} again. Preview again to see it "
                                "excluded.").format(name=Path(a['source_dir']).name))
