"""gui/overview.py - The Overview page: drop or choose a folder, pick a profile, Preview, then Apply."""
from __future__ import annotations

import html
import os
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QStackedWidget, QTableView, QVBoxLayout, QWidget)

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


# Look Up's sources (List first): the profile's, or one of them alone
LOOK_UP = (("profile", N_("Profile's sources")), ("musicbrainz_only", N_("MusicBrainz only")),
           ("musicbrainz_preferred", N_("MusicBrainz preferred")), ("discogs_preferred", N_("Discogs preferred")),
           ("discogs_only", N_("Discogs only")), ("web", N_("Web only")))


RETRY_CODES = {"musicbrainz_unavailable", "discogs_unavailable", "rate_limited", "missing_credentials"}


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
        self.looked = {}            # album key -> how it was looked up (profile, sources): not again
        self._lookup_n = 0
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("Overview"),
                              tr("Choose music, pick a profile, preview what will change, then apply.")))
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
        rl.addWidget(strip)
        # which databases to ask, and the button that asks them (nothing is looked up on a drop)
        look = QHBoxLayout()
        look.setContentsMargins(0, 0, 0, 0)
        self.sources_label = QLabel(tr("Sources"))
        look.addWidget(self.sources_label)
        from .rows import Popup
        from PySide6.QtWidgets import QComboBox
        self.sources = Popup()
        self.sources.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sources.setMinimumContentsLength(14)            # (a narrow window: the words cut, the list whole)
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
        look.addWidget(self.look_btn)
        # (a choice other than the profile's is for this look-up only; this saves it into the profile)
        self.keep_link = QLabel(f'<a href="keep">{tr("Make It the Profile’s")}</a>')
        self.keep_link.setTextFormat(Qt.RichText)
        self.keep_link.setAccessibleName(tr("Make this choice of sources the profile's own"))
        self.keep_link.linkActivated.connect(lambda _h: self.keep_sources())
        self.keep_link.hide()
        look.addWidget(self.keep_link)
        look.addStretch(1)
        rl.addLayout(look)
        self.profile.currentTextChanged.connect(lambda _t: self.reset_sources())
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
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # (several: apply, leave out, remove)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setTextElideMode(Qt.ElideRight)        # (the album's name first, its artist after)
        h = self.table.horizontalHeader()
        left_header(self.table)
        h.setMinimumSectionSize(48)
        for c in range(len(PlanModel.COLS)):          # the album folder and Why take what is left
            h.setSectionResizeMode(c, QHeaderView.Stretch if c in (1, 5) else QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Interactive)     # (fitted by fit_status: cut when narrow)
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
        from PySide6.QtGui import QShortcut, QKeySequence
        rm = QShortcut(QKeySequence.Delete, self.table, context=Qt.WidgetShortcut)   # (the list's own Delete)
        rm.activated.connect(self.remove_selected)
        rl.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "hint")
        self.summary.setAccessibleDescription(tr("Summary"))    # (no fixed name: the counts are what is read)
        rl.addWidget(self.summary)
        self.views.addWidget(self.results)
        self.detail = win.inspector.full                  # (the inspector's Evidence > Full preview)
        self.refresh_profiles()
        self.busy(False)

    def filter(self, text):
        self.proxy.setFilterFixedString(text)

    # page widths under which lower-priority columns give way (the inspector shows them all):
    # Why under 860 px (1280 px with the inspector open), Source and Score under 600 (960 px)
    NARROW = {5: 860, 3: 600, 2: 420}                  # (Source stays longest: which database said it)
    STATUS_MOST = 150                                 # under 600 px the Status column is cut ("Changed sin…",
                                                      # the whole word in its tooltip): the artist stays in view

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for c, w in self.NARROW.items():
            hide = self.width() < w
            if self.table.isColumnHidden(c) != hide:
                self.table.setColumnHidden(c, hide)
        self.fit_status()

    def fit_status(self):
        want = max(self.table.sizeHintForColumn(0), self.table.horizontalHeader().sectionSizeHint(0))
        if self.width() < 600:
            want = min(want, self.STATUS_MOST)
        if self.table.columnWidth(0) != want:
            self.table.horizontalHeader().resizeSection(0, want)

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
            look.setEnabled(not self.running)
            mine = [look.menuAction()]
            for text, slot, on in ((tr("Apply Selected Albums…") if n > 1 else tr("Apply This Album…"),
                                    self.apply_selected, any(can_apply(a) for a in chosen) and not self.running),
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
        self.apply()

    def remove_selected(self):
        """Take the selected albums out of this preview (their folders and files are not touched)."""
        chosen = self.selected_albums()
        if not chosen or self.running or not self.plan:
            return
        ids = {id(a) for a in chosen}
        self.plan["albums"] = [a for a in self.plan["albums"] if id(a) not in ids]
        self.model.set_plan(self.plan)
        self.summary.setText(tr("{0} taken out of this preview; nothing on disk changed.")
                             .format(plural(len(chosen), tr("album"), tr("albums"))))
        self.win.update_album_actions()

    def refresh_profiles(self):
        cur = self.profile.currentText()
        self.profile.clear()
        self.profile.addItems(profile_names())
        if cur:
            self.profile.setCurrentText(cur)

    def busy(self, on):
        """Preview and Apply are enabled by the window (on this page only); while work runs no
        other folder can be chosen or dropped."""
        self.running = on
        if not on:
            self._lookup_n = 0                                # (the next run says its own progress)
        self.choose_btn.setEnabled(not on)
        self.drop.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        self.win.show_progress(on)
        self.win.update_album_actions()

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
        self.reset_sources()                                 # (a new folder: the profile's sources again)
        self.where.setText(path)
        self.views.setCurrentWidget(self.results)
        if self.win.stack.currentWidget() is not self:
            self.win.go("Overview")
        self.win.place_inspector()                          # (something to inspect now)
        self.plan = None
        self.model.set_plan(None)
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
            return engine.Engine(prof).listing(path, within=within or None)
        self.busy(True)
        self.summary.setText(tr("Finding the albums… nothing is looked up yet."))
        self.worker = start(self, work, self.listed, self.progress, self.oops)

    def listed(self, plan):
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.model.set_plan(plan)
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

    def lookup_stamp(self, prof, mode) -> str:
        """What a look-up depends on (the profile as it is now, and the sources): the same again
        finds the same, so it is not asked of the databases twice."""
        import json
        return json.dumps([self.profile.currentText(), mode, self.ocr.isChecked(), prof], sort_keys=True,
                          default=str)

    def reload(self):
        """Refresh (Ctrl+R): look the selected albums (none selected: all of them) up again, even
        those already looked up the same way."""
        if self.running or not self.plan:
            return
        chosen = self.selected_albums() if self.table.selectionModel().hasSelection() else None
        self.look_up(self.sources.currentData() or "profile", chosen or list(self.plan["albums"]), again=True)

    def look_up(self, mode="profile", albums=None, again=False):
        """Look up the selected albums (or ALBUMS) with the profile's sources, or only
        MusicBrainz, only Discogs, or only the web; what they find replaces their rows.
        An album already looked up the same way is skipped unless AGAIN (Refresh)."""
        import engine
        chosen = albums if albums is not None else self.selected_albums()
        if not chosen or self.running or not self.path:
            return
        try:
            prof = profile_for(load_profile(self.profile.currentText()), mode)
        except pf.ProfileError as exc:
            sheets.warning(self, tr("This profile cannot be used."), str(exc))
            return
        stamp = self.lookup_stamp(prof, mode)
        keys = {a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or []) for a in chosen}
        done = {k for k in keys if self.looked.get(k) == stamp} if not again else set()
        if done and done == keys:
            n = len(keys)
            if not sheets.question(self, tr("{0} already looked up with {1}.").format(
                    tr("This album is") if n == 1 else tr("These {0} albums are").format(n),
                    self.sources_words(mode)),
                    tr("Looking up again asks the databases once more (Refresh, Ctrl+R, does the same)."),
                    action=tr("Look Up Again")):
                return
            done = set()
        keys -= done
        skipped = len(done)
        path, ocr, edits = self.path, self.ocr.isChecked(), self.win.inspector.edits_for_preview()

        def work(w):
            e = engine.Engine(prof, ocr=True if ocr else None)
            e.edits = edits
            return e.plan(path, progress=w.progress.emit, cancel=lambda: w.cancelled, only=keys)
        self.busy(True)
        self.looked_with = self.sources_words(mode)
        self._lookup_n = len(keys)
        self._lookup_skipped = skipped
        self.summary.setText(self.lookup_words(0, ""))
        self.worker = start(self, work, lambda plan: self.looked_up(plan, keys, stamp), self.progress, self.oops)

    def lookup_words(self, i, name) -> str:
        """The line under the list while albums are looked up: which one of how many."""
        n = self._lookup_n
        head = tr("Looking up album {0} of {n} with {w}").format(i + 1, n=n, w=self.looked_with) if name else \
            tr("Looking up {0} with {1}").format(plural(n, tr("album"), tr("albums")), self.looked_with)
        tail = tr(": {0}").format(name) if name else ""
        skipped = getattr(self, "_lookup_skipped", 0)
        note = tr(" ({0} already looked up, skipped)").format(skipped) if skipped else ""
        return head + tail + "…" + note + tr(" Nothing is changed while previewing.")

    def remember_looked(self, albums, stamp):
        """The albums found, as looked up with STAMP (cancelled: only those it reached); not one
        that failed or a database was busy or not answering for: that one is worth asking again."""
        import engine
        if stamp is None:
            return
        for a in albums:
            codes = set(a.get("reason_codes") or [])
            if a.get("status") == "listed" or codes & RETRY_CODES or str(a.get("reason", "")).startswith("error:"):
                continue
            self.looked[a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or [])] = stamp

    def looked_up(self, new, keys, stamp=None):
        """The albums looked up take the places of their rows; the others stay as they were."""
        import engine
        found = list(new["albums"])
        self.remember_looked(found, stamp)
        out, placed = [], False
        for a in (self.plan or {}).get("albums") or []:
            k = a.get("key") or engine.group_key(a["source_dir"], a.get("album_files") or [])
            if k in keys:
                if not placed:                             # (where the first of them was)
                    out += found
                    placed = True
                continue
            out.append(a)
        if not placed:
            out += found
        self.plan = dict(new, albums=out, listed=any(a["status"] == "listed" for a in out))
        self.planned(self.plan)

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
        if not self.set_path(str(folder), {key: choice}):
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
            if not sheets.confirm(self, tr("Discard your edits of {0}?").format('1 album' if n == 1 else f'{n} albums'),
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
        self.bar.setFormat(tr("{0} of {n}").format(i + 1, n=n))
        words = tr("{0} of {n}: {name}").format(i + 1, n=n, name=Path(what).name)
        self.bar.setToolTip(words)
        self.bar.setAccessibleDescription(words)
        if getattr(self, "_lookup_n", 0) and self.running:  # (Look Up: which album, under the list)
            self.summary.setText(self.lookup_words(i, Path(what).name))
        elif self.running:
            self.summary.setText(tr("Identifying album {0} of {n}: {name}… nothing is changed while previewing.")
                                 .format(i + 1, n=n, name=Path(what).name))

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
        if getattr(self, "within", None) and self.plan:      # (several folders: just the albums listed)
            self.look_up(self.sources.currentData() or "profile", list(self.plan["albums"]))
            return
        mode = self.sources.currentData() or "profile"
        try:
            prof = profile_for(load_profile(self.profile.currentText()), mode)   # (Look up with: its choice)
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
            return e.plan(path, progress=w.progress.emit, cancel=lambda: w.cancelled)
        self.busy(True)
        self.summary.setText(tr("Identifying with {0}… nothing is changed while previewing.").format(self.looked_with))
        stamp = None if forced else self.lookup_stamp(prof, mode)    # (a release you chose: not the same)

        def done(plan):
            self.looked = {}
            self.remember_looked(plan.get("albums") or [], stamp)
            self.planned(plan)
        self.worker = start(self, work, done, self.progress, self.oops)

    def planned(self, plan):
        self.plan = plan
        self.views.setCurrentWidget(self.results)
        self.win.place_inspector()
        self.model.set_plan(plan)
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
        self.summary.setText("Stopped: " + msg.splitlines()[0])
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
            items.append(f"and {n - APPLY_LISTED} more")
        # (rich text: a wrapped line hangs under its words, not under the bullet)
        body = (f"<p>{html.escape(what)}</p><ul style='margin-left:-24px; margin-top:0'>"
                + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        mine = sum(1 for a in albums if a.get("edited") and not planmod.edit_empty(a["edited"]))
        if mine:                                      # (values you typed or a cover you chose, not the match's)
            body += tr("<p>Includes your own edits of {0}: tags or a cover you chose, marked with a pencil in the "
                       "inspector.</p>").format(plural(mine, tr("album"), tr("albums")))
        word = tr("Album") if n == 1 else tr("Albums")
        return (tr("Apply the preview to {n} {0}?").format(word.lower(), n=n), body,
                tr("Every change can be undone: Edit > Undo Last Job, or Jobs."), f"Apply {n} {word}")

    def apply(self):
        n = self.actionable()
        if not n or self.running or self.edit_pending():
            return
        # always asked here: the profile's "Apply without asking" is for its watched folders only
        if not sheets.confirm(self, *self.apply_words()) or self.edit_pending():
            return
        chosen = self.to_apply()
        self.release_files()
        # (only the ticked albums go to the executor; what it reports is mapped back to the list)
        self._applied_rows = [i for i, a in enumerate(self.plan["albums"]) if any(a is c for c in chosen)]
        plan = dict(self.plan, albums=[self.plan["albums"][i] for i in self._applied_rows])

        def work(w):
            import executor
            return executor.Executor(plan, cancel=lambda: w.cancelled, progress=w.progress.emit).run()
        self.busy(True)
        self.summary.setText(tr("Applying…"))
        self.worker = start(self, work, self.applied, self.progress, self.oops)

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
        rep = run_report(dict(self.plan, albums=[self.plan["albums"][i] for i in rows]), res)
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
        self.summary.setText(f"Job {res['job']}: " + counts_text(res["counts"]) + tr(". Undo it from Jobs."))
        left = [a for a in self.plan["albums"] if can_apply(a)]
        if left:                                          # (albums left out stay, to apply later)
            self.summary.setText(self.summary.text() + " " + tr("{0} left to apply.").format(
                plural(len(left), tr("album"), tr("albums"))))
        else:
            self.plan = None
        self.busy(False)
        self.service_status(force=True)
        notify(self.win, "finished", tr("Apply finished"), counts_text(res["counts"]))
        self.win.pages["Jobs"].reload()
        self.win.refresh_attention_badge(self.path)          # (Apply marks albums Unresolved on disk)
        if rep is not None:
            self.win.show_run_report(rep)                  # (what was done, what is left for you)

    def cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self.summary.setText(tr("Cancelling after the current album…"))

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
            one = dict(self.plan or {"job": "", "profile": "", "operation": ""}, albums=[a], summary={a["status"]: 1})
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
        self.preview()

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
