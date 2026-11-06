"""gui/window.py - The main window: a unified toolbar, the source-list sidebar, the pages, the
inspector on the right, the player bar under the pages, the menu bar and the status bar; and main().

It wires the M8 tools in: File > Look Up CD (gui.browser), Album > Match Files Manually
(gui.manual_match), Album > Add to Collection and Submit Ratings (gui.account), Album > Submit
AcoustIDs (gui.acoustid_submit), Controls (gui.player), the browser integration's server
(gui.browser.install) and folders dropped on the window (manual_match.add_dropped)."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QMainWindow, QMenu, QProgressBar, QSplitter, QStackedWidget, QStatusBar,
                               QToolButton, QVBoxLayout, QWidget)

from . import actions as actmod
from . import account, dialogs, i18n, icons, search, sheets, style
from . import acoustid_submit, browser, manual_match
from . import report as report_mod
from . import setup as setup_mod
from .i18n import N_, tr
from . import player as player_mod
from .player import PlayerBar
from .common import (APP, HERE, _gui_prefs, _set_gui_pref, music_paths, open_folder, planmod, plural,
                     read_desktop_motion, search_web)
from .work import _alive
from .widgets import SourceList
from .inspector import TABS, Inspector
from .overview import Overview
from .jobs import Jobs
from .watched import Watched
from .profiles import Profiles
from .attention import Attention
from .history import History


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------
SECTIONS = ((N_("Library"),
             ((N_("Overview"), Overview, "library"), (N_("Needs attention"), Attention, "triangle-alert"),
                         (N_("History"), History, "history"))),
            (N_("Automation"), ((N_("Watched folders"), Watched, "folder-sync"), (N_("Jobs"), Jobs, "list-checks"))))
# pages shown in Preferences (its Profiles and Setup tabs), not in the sidebar: go() opens them there
PREF_PAGES = ((N_("Profiles"), Profiles, "sliders-horizontal"), (N_("Setup"), setup_mod.Setup, "key-round"))
PAGES = tuple((name, cls) for _s, rows in SECTIONS for name, cls, _i in rows) + \
    tuple((name, cls) for name, cls, _i in PREF_PAGES)
IN_PREFERENCES = tuple(name for name, _c, _i in PREF_PAGES)
ICONS = {name: icon for _s, rows in SECTIONS for name, _c, icon in rows}
ICONS.update({name: icon for name, _c, icon in PREF_PAGES})
SEARCH_HINT = {"Overview": N_("Search albums"), "Needs attention": N_("Search reasons, folders"),
               "History": N_("Search exclusions"), "Watched folders": N_("Search folders"), "Jobs": N_("Search jobs"),
               "Profiles": N_("Search profiles"), "Setup": N_("Search setup")}
ALBUM_PAGES = ("Overview", "Needs attention")         # the pages the inspector shows albums for
BADGE_NOUN = {"Needs attention": "albums", "Watched folders": N_("albums waiting")}
ISSUES = "https://github.com/herculepirate/tagmender/issues"
# the content's narrowest with the inspector open (on the 8-pt grid): at 960 px the sidebar keeps
# its 220 and the inspector its 280, with room for the two hairlines between them
CONTENT_MIN = 456
_styled = False
WINDOW_PREF = "window"                                # gui.json: the window's size and place, as last left
CD_DRIVE_PREF = "cd_drive"                             # gui.json: the drive Look Up CD reads ("" libdiscid's)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        global _styled
        if not _styled:                                     # (once: main() or the first window)
            _styled = True
            style.apply(QApplication.instance(), _gui_prefs().get("appearance", "system"))
        self.setWindowTitle(APP + "[*]")                      # ("[*]": unsaved profile changes)
        self.setObjectName("main")
        self.setMinimumSize(960, 600)
        self.resize(1180, 760)
        self.setDockOptions(QMainWindow.AnimatedDocks)
        self.setAcceptDrops(True)
        self.prefs_window = None
        self.pages = {}
        # commands, menus and the toolbar first: the pages use them
        self.commands = actmod.build(self, [(n, ICONS[n]) for n, _c in PAGES])
        self.menus = actmod.build_menus(self, self.commands, [(n, ICONS[n]) for n, _c in PAGES])
        self.toolbar = actmod.build_toolbar(self, self.commands)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)
        self.setUnifiedTitleAndToolBarOnMac(True)
        # the toolbar's progress: a thin bar (style.py draws it 6 px high, too low for words in
        # it) and the count beside it ("3 of 16"; the album's name is below the list)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(120)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName(tr("Progress"))
        self.progress_count = QLabel("")
        self.progress_count.setProperty("role", "caption")
        self.progress_count.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.progress_box = QWidget()
        pl = QHBoxLayout(self.progress_box)
        pl.setContentsMargins(6, 0, 6, 0)
        pl.setSpacing(8)
        pl.addWidget(self.progress, 0, Qt.AlignVCenter)
        pl.addWidget(self.progress_count, 0, Qt.AlignVCenter)
        self.progress_action = None
        self.profile_box = QComboBox()
        self.profile_box.setAccessibleName(tr("Profile"))
        self.profile_box.setToolTip(tr("The profile the preview uses"))
        self.profile_box.setFixedWidth(160)
        self.search_box = QLineEdit()
        self.search_box.setAccessibleName(tr("Search"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.addAction(icons.icon("search", "muted"), QLineEdit.LeadingPosition)
        self.search_box.setMinimumWidth(140)
        self.search_box.setMaximumWidth(200)
        self.search_box.setAcceptDrops(False)                 # (a folder dropped on it is previewed, not typed)
        self.search_box.textChanged.connect(self.search)
        actmod.toolbar_end(self.toolbar, [self.progress_box, tr("Profile"), self.profile_box, self.search_box],
                           self.commands)
        self.progress_action = next(a for a in self.toolbar.actions()
                                    if self.toolbar.widgetForAction(a) is self.progress_box)
        self.progress_action.setVisible(False)
        # the Profile label and pop-up: shown on Overview only (elsewhere they would pick nothing)
        at = [a for a in self.toolbar.actions() if self.toolbar.widgetForAction(a) is self.profile_box][0]
        i = self.toolbar.actions().index(at)
        self.profile_actions = (self.toolbar.actions()[i - 1], at)
        # the inspector, the sidebar and the pages
        self.inspector = Inspector(self)
        self.inspector_extras()                               # (Info…, and the release button's menu)
        self.inspector.tags.addActions([self.commands[k] for k in actmod.SCOPED])   # (their keys: in the Tags)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector)
        self.inspector.visibilityChanged.connect(self.inspector_shown)
        self.nav = SourceList()
        w, lo, hi = style.SIDEBAR
        self.nav.setMinimumWidth(lo)
        self.nav.setMaximumWidth(hi)
        for section, rows in SECTIONS:
            self.nav.add_header(section)
            for name, _cls, icon in rows:
                self.nav.add_page(name, icon)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(420)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(1)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.nav)
        self.split.addWidget(self.stack)
        self.split.setStretchFactor(1, 1)
        self.split.splitterMoved.connect(self.sidebar_dragged)
        central = QWidget()                                  # (the pages, the player bar under them)
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.split, 1)
        self.player = PlayerBar(central)                     # (hidden until something plays)
        cl.addWidget(self.player)
        self.setCentralWidget(central)
        bar = QStatusBar()                                   # (shown only while it has a message)
        bar.setSizeGripEnabled(False)
        bar.messageChanged.connect(lambda msg: bar.setVisible(bool(msg)))
        bar.hide()
        self.setStatusBar(bar)
        self.inspector_wanted = True                          # the person's choice (View > Show Inspector)
        self._placing = False
        self._nav_dragged = False                             # (the widths saved are the person's, never
        self._fit_width = None                                #  what fit_docks squeezed them to)
        for name, cls in PAGES:
            page = cls(self)
            page.layout().setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, 16)
            page.layout().setSpacing(16)
            self.pages[name] = page
            if name not in IN_PREFERENCES:
                self.stack.addWidget(page)
            else:                                             # (a tab of Preferences once it opens; till
                page.setParent(self)                          #  then the window's, hidden: deleted with it)
                page.hide()
        # an online service busy or down during a run: said on every page, beside the status line
        self.service_badge = QLabel("")
        self.service_badge.setProperty("role", "attention")
        self.service_badge.setAccessibleName(tr("Online services"))
        self.service_badge.hide()
        self.statusBar().addPermanentWidget(self.service_badge)
        self.connect_actions()
        self.connect_player()
        self.inspector.edited.connect(self.pages["Overview"].edited)    # (its row, counts and Apply)
        browser.install(self)                                 # (listens only when Preferences turned it on)
        self.nav.currentRowChanged.connect(self.nav_changed)
        self.restore()
        self.order_focus()
        self.nav.setFocus()
        import executor
        if set(executor.pending_jobs()) - set(executor.busy_jobs()):
            self.statusBar().showMessage(tr("A job was interrupted. Recover it under Jobs (Tools > Recover "
                                            "Interrupted Jobs…)."))

    # ---- commands ---------------------------------------------------------
    def act(self, key):
        return self.commands[key]

    def connect_actions(self):
        ov = lambda: self.pages["Overview"]  # noqa: E731
        run = {
            "add_folder": self.add_folder,
            "preview": lambda: ov().preview(),
            "apply": lambda: ov().apply(),
            "cancel": self.cancel,
            "watch_folder": lambda: (self.go("Watched folders"), self.pages["Watched folders"].add()),
            "new_profile": lambda: (self.go("Profiles"), self.pages["Profiles"].new()),
            "save_profile": lambda: self.pages["Profiles"].save(),
            "close_window": lambda: (QApplication.activeWindow() or self).close(),
            "quit": self.close,
            "undo_edit": self.undo_edit,
            "undo_job": lambda: (self.player.release(), self.go("Jobs"),
                                 self.pages["Jobs"].undo_last()),   # (its outcome in view)
            "undo_selected": lambda: (self.player.release(), self.pages["Jobs"].undo()),
            "show_job_albums": lambda: self.pages["Jobs"].show_albums(),
            "job_details": self.job_details,
            "show_report": lambda: report_mod.for_window(self),
            "cut": lambda: self.edit("cut"),
            "copy": lambda: self.edit("copy"),
            "paste": lambda: self.edit("paste"),
            "select_all": lambda: self.edit("selectAll"),
            "copy_path": self.copy_path,
            "find": lambda: (self.search_box.setFocus(), self.search_box.selectAll()),
            "preferences": self.preferences,
            "setup": lambda: setup_mod.open_setup(self),
            "refresh": self.refresh,
            "tab_tags": lambda: self.inspector.show_tab("Tags"),
            "tab_artwork": lambda: self.inspector.show_tab("Artwork"),
            "tab_evidence": lambda: self.inspector.show_tab("Evidence"),
            "use_release": lambda: ov().choose_release(),
            **search.handlers(ov),                               # Search for Release, Other Versions, Pressing
            **manual_match.handlers(ov),                         # Match Files Manually
            "lookup_cd": self.lookup_cd,
            "add_to_collection": self.add_to_collection,
            "submit_ratings": self.submit_ratings,
            "submit_acoustids": self.submit_acoustids,
            "play_pause": self.play_pause,
            "next_track": lambda: self.player.next(),
            "previous_track": lambda: self.player.previous(),
            "stop_playing": lambda: self.player.close_player(),
            "skip_forward": lambda: self.player.skip(player_mod.skip_ms()),
            "skip_back": lambda: self.player.skip(-player_mod.skip_ms()),
            "volume_up": lambda: self.player.step_volume(player_mod.VOLUME_STEP),
            "volume_down": lambda: self.player.step_volume(-player_mod.VOLUME_STEP),
            "mute": lambda: self.player.mute(),
            "file_info": self.file_info,
            "edit_tag": lambda: self.inspector.tags.edit_key(),
            "revert_tag": lambda: self.inspector.tags.revert_selected(),
            "use_suggested": lambda: self.inspector.tags.use_suggested(),
            "remove_tag": lambda: self.inspector.tags.remove_selected(),
            "add_tag": lambda: (self.inspector.show_tab("Tags"), self.inspector.tags.add_tag()),
            **{k: (lambda n=n: self.rate(n)) for k, n in actmod.RATINGS.items()},
            "find_covers": lambda: (self.inspector.show_tab("Artwork"), self.inspector.art.find_covers()),
            "choose_cover": lambda: (self.inspector.show_tab("Artwork"), self.inspector.art.choose_file()),
            "cover_link": lambda: (self.inspector.show_tab("Artwork"), self.inspector.art.ask_url()),
            "suggested_cover": lambda: self.inspector.art.clear(),
            "tags_from_filenames": self.tags_from_filenames,
            "process_anyway": lambda: ov().process_anyway(),
            "exclude_again": lambda: ov().exclude_again(),
            "retry": lambda: self.pages["Needs attention"].retry(),
            "search_web": lambda: self.album_do("web"),
            "open_folder": lambda: self.album_do("open"),
            "check_now": lambda: (self.go("Watched folders"), self.pages["Watched folders"].check_now()),
            "change_watch": lambda: self.pages["Watched folders"].change(),
            "stop_watch": lambda: self.pages["Watched folders"].remove(),
            "process_again": lambda: self.pages["Watched folders"].process_again(),
            "run_in_background": lambda: (self.go("Watched folders"), self.pages["Watched folders"].toggle_service()),
            "show_plan": lambda: self.pages["Watched folders"].show_plan(),
            "apply_plan": lambda: (self.player.release(), self.pages["Watched folders"].apply_one()),
            "apply_all_plans": lambda: (self.player.release(), self.pages["Watched folders"].apply_all()),
            "dismiss_plan": lambda: self.pages["Watched folders"].dismiss(),
            "new_rule": lambda: (self.go("Profiles"), self.pages["Profiles"].add_rule()),
            "edit_rule": lambda: self.pages["Profiles"].edit_rule(),
            "delete_rule": lambda: self.pages["Profiles"].remove_rule(),
            "test_rules": lambda: (self.go("Profiles"), self.pages["Profiles"].test_rules()),
            "tagscript": lambda: (self.go("Profiles"), self.pages["Profiles"].show_picard_script()),
            "edit_scripts": self.edit_scripts,
            "recover": lambda: (self.player.release(), self.go("Jobs"), self.pages["Jobs"].ask_recover()),
            "run_checks": lambda: (self.preferences().show_tab("Checks"), self.prefs_window.run_checks()),
            "export": lambda: (self.preferences().show_tab("Checks"), self.prefs_window.export()),
            "show_log": lambda: dialogs.show_logs(self),
            "minimize": self.showMinimized,
            "zoom": lambda: self.showNormal() if self.isMaximized() else self.showMaximized(),
            "main_window": lambda: (self.showNormal() if self.isMinimized() else None, self.raise_(),
                                    self.activateWindow()),
            "help": self.help,
            "shortcuts": lambda: sheets.show_text(self, tr("Keyboard shortcuts"), actmod.shortcut_text(self.commands),
                                                  tr("Every command is also in the menu bar (Alt and the "
                                                     "underlined letter).")),
            "report": self.report,
            "about": self.about,
        }
        for key, fn in run.items():
            self.commands[key].triggered.connect(lambda _=False, f=fn: f())
        for name, _c in PAGES:
            self.commands[f"page:{name}"].triggered.connect(lambda _=False, n=name: self.go(n))
        a = self.commands["read_scans"]
        ocr = self.pages["Overview"].ocr
        a.toggled.connect(ocr.setChecked)
        ocr.toggled.connect(a.setChecked)
        a = self.commands["show_sidebar"]
        a.setChecked(True)
        a.toggled.connect(self.nav.setVisible)
        a = self.commands["show_inspector"]
        a.toggled.connect(self.toggle_inspector)
        a = self.commands["toggle_rule"]
        a.triggered.connect(lambda _=False: self.pages["Profiles"].toggle_selected_rule())
        self.inspector.buttons["open"].clicked.connect(lambda: self.album_do("open"))
        self.inspector.buttons["web"].clicked.connect(lambda: self.album_do("web"))
        live = self.pages["Watched folders"].live
        live.toggled.connect(lambda on: self.prefs_window and self.prefs_window.general.watch.setChecked(on))
        self.pages["Profiles"].tabs.currentChanged.connect(lambda _i: self.update_album_actions())
        QApplication.instance().focusChanged.connect(self.focus_changed)   # (a method: let go with the window)
        for v in self.findChildren(QAbstractItemView):          # (a row selected in the focused table)
            if v.selectionModel() is not None:
                v.selectionModel().selectionChanged.connect(lambda *_: self.update_edit_actions())
        self.menus["Edit"].aboutToShow.connect(self.update_edit_actions)

    EDIT = {"cut": "cut", "copy": "copy", "paste": "paste", "select_all": "selectAll"}

    def edit_target(self, what):
        """What Cut, Copy, Paste or Select All acts on: the focused widget's own method, or a
        table's selected row (Copy only); None when nothing can take it."""
        w = QApplication.focusWidget()
        if isinstance(w, QAbstractItemView) and not isinstance(w, QListView):
            if what == "copy":
                return self.copy_row if self.row_text(w) else None
            if what == "selectAll" and w.selectionMode() in (QAbstractItemView.ExtendedSelection,
                                                             QAbstractItemView.MultiSelection):
                return w.selectAll
            return None
        fn = getattr(w, what, None)
        if not callable(fn):
            return None
        ro = getattr(w, "isReadOnly", lambda: False)()
        if what in ("cut", "paste") and ro:
            return None
        return fn

    def focus_changed(self, _old, _new):
        self.update_edit_actions()

    def update_edit_actions(self):
        """Cut, Copy, Paste and Select All are on only where they can act (the focused widget)."""
        if not hasattr(self, "commands"):
            return
        for key, what in self.EDIT.items():
            self.commands[key].setEnabled(self.edit_target(what) is not None)

    def edit(self, what):
        """Cut, Copy, Paste, Select All on the focused text (a field takes its own keys first);
        Copy on a table copies its selected row (Overview: the album's folder)."""
        fn = self.edit_target(what)
        if fn is not None:
            fn()

    @staticmethod
    def row_text(view):
        """A table's selected row: the folder behind it (Qt.UserRole of a folder cell), else its words."""
        sel = view.selectionModel()
        if sel is None or not sel.hasSelection():
            return ""
        idx = view.currentIndex()
        if not idx.isValid():
            return ""
        model = view.model()
        cells = [model.index(idx.row(), c) for c in range(model.columnCount())
                 if not view.isColumnHidden(c)]
        for c in cells:
            v = c.data(Qt.UserRole)
            if isinstance(v, str) and os.path.isabs(v):
                return v
        return "\t".join(str(c.data(Qt.DisplayRole) or "") for c in cells).strip()

    def copy_row(self):
        w = QApplication.focusWidget()
        if w is self.pages["Overview"].table:
            return self.copy_path()
        text = self.row_text(w) if isinstance(w, QAbstractItemView) else ""
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(tr("Copied the folder.") if os.path.isabs(text) else tr("Copied the row."),
                                         3000)

    def album(self):
        """("overview", album) or ("attention", folder) for the page shown, else (None, None)."""
        page = self.stack.currentWidget()
        if page is None:
            return None, None
        if page is self.pages.get("Overview"):
            a = page.selected()
            return ("overview", a) if a else (None, None)
        if page is self.pages.get("Needs attention"):
            f = page.folder() if page.table.selectionModel().hasSelection() else None
            return ("attention", f) if f else (None, None)
        return None, None

    def album_do(self, what):
        kind, x = self.album()
        if not kind:
            return
        folder = x["source_dir"] if kind == "overview" else x
        if what == "open":
            open_folder(folder)
        else:
            search_web(x if kind == "overview" else {"source_dir": folder})

    def copy_path(self):
        kind, x = self.album()
        if kind:
            QApplication.clipboard().setText(x["source_dir"] if kind == "overview" else x)
            self.statusBar().showMessage(tr("Copied the album's folder."), 3000)

    # ---- the M7 sheets: File Info, Tags from File Names, Job Details ----------------
    def inspector_extras(self):
        """The inspector's header: Change Release, one button whose menu is Album > Change Release
        (a pasted link, search, other versions, your pressing), its arrow drawn inside the button.
        (Info… is not a button there: at 280 px the header has no room; it is in the Album menu,
        the table's menu and Ctrl+I.)"""
        b = self.inspector.buttons["release"]
        m = QMenu(b)
        for k in actmod.RELEASE_MENU:
            m.addAction(self.commands[k])
        actmod.mac_menu(m)
        b.setMenu(m)
        b.setPopupMode(QToolButton.InstantPopup)
        b.setToolTip(tr("Change Release: paste a link, search, or pick another version or your pressing"))
        self.style_release_arrow()

    def style_release_arrow(self):
        """The release button's arrow as the pop-ups' chevron, inside the button's own box."""
        down = icons.file("chevron-down", "muted", 10, 2.5)
        self.inspector.buttons["release"].setStyleSheet(
            "QToolButton { padding-right: 14px; } "
            "QToolButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; "
            f"right: 4px; width: 10px; height: 10px; image: url({down}); }}")

    def info_file(self):
        """The file File Info reads: the one the inspector's Tags show, else the album's first."""
        kind, a = self.album()
        if kind != "overview":
            return None
        src = self.inspector.tags.file_src() if self.inspector.album is a else None
        files = a.get("files") or []
        return src or (files[0]["src"] if files else None)

    def undo_edit(self):
        """Edit > Undo Edit: your last edit of the preview taken back (the inspector's history)."""
        n = self.inspector.undo_edit()
        if n:
            self.statusBar().showMessage(tr("Your last edit was taken back ({0}). Nothing was "
                                            "written.").format(plural(n, tr('album'), tr('albums'))), 5000)

    def update_tag_actions(self):
        """Album > Tag and Artwork, and Undo Edit: on for the album the inspector shows on
        Overview, when it can be edited (its Tags' buttons say which apply to the selected row)."""
        if not hasattr(self, "pages") or "Overview" not in self.pages or not hasattr(self, "stack"):
            return
        ov = self.pages["Overview"]
        kind, x = self.album()
        insp = self.inspector
        here = kind == "overview" and not ov.running and insp.album is x
        tv, art = insp.tags, insp.art
        tags_on = here and tv.editable()
        for k, b in (("edit_tag", "edit"), ("revert_tag", "revert"), ("use_suggested", "suggested"),
                     ("remove_tag", "remove"), ("add_tag", "add")):
            self.commands[k].setEnabled(tags_on and tv.buttons[b].isEnabled())
        box = getattr(tv, "rating", None)
        rate_on = here and box is not None and box.isVisibleTo(insp) and box.stars.isEnabled()
        for k in actmod.RATINGS:
            self.commands[k].setEnabled(rate_on)
        cover_on = here and art.album is x and art.editable()
        for k in ("find_covers", "choose_cover", "cover_link"):
            self.commands[k].setEnabled(cover_on)
        mine = ((x or {}).get("artwork") or {}).get("by") == planmod.BY_YOU if kind == "overview" else False
        self.commands["suggested_cover"].setEnabled(cover_on and mine)
        self.commands["undo_edit"].setEnabled(self.album_page() and insp.can_undo_edit())

    def rate(self, n):
        """Album > Tag > Rating: the inspector's stars set to n (None: no rating), as clicking them does."""
        box = getattr(self.inspector.tags, "rating", None)
        if box is not None and box.stars.isEnabled():
            self.inspector.show_tab("Tags")
            box.stars.set_value(n, emit=True)

    def file_info(self):
        path = self.info_file()
        return dialogs.show_file_info(self, path) if path else None

    def tags_from_filenames(self):
        """The selected album's file names, read as tags into the preview as your edits (the
        inspector's store: a new preview keeps them); nothing is written until Apply."""
        ov = self.pages["Overview"]
        a = ov.selected()
        if not a or ov.running or not ov.plan:
            return None
        plan = ov.plan

        def done(_plan, result):
            n, out, _store = result
            msg = (tr("Tags from the file names are in the preview of {0}, in place of earlier "
                      "values of those tags; nothing is written until you apply. Edit > Undo Edit (Ctrl+Z) takes "
                      "them back.").format(plural(n, tr('album'),
                                               tr('albums'))) if n else tr("No tags were taken from the file names."))
            if out:
                msg += tr(" {0} left out: {1}.").format(plural(len(out), tr('file'), tr('files')), out[0][1])
            self.statusBar().showMessage(msg, 8000)
            if plan is ov.plan:
                for x in plan["albums"]:
                    ov.edited(x)

        def use(overrides):
            if ov.running or plan is not ov.plan:
                self.statusBar().showMessage(tr(self.BUSY) if ov.running else
                                             tr("The preview changed: choose Tags from File Names again."), 5000)
                return
            insp = self.inspector
            insp.remember()                                          # (Undo Edit takes them back)
            reapply = insp.reapply if insp.plan is plan else None   # (its own plan: the edit goes its way)
            dialogs.apply_in_background(self, plan, overrides, done, store=insp.edits, reapply=reapply)
        return dialogs.show_tags_from_filenames(self, dialogs.plan_files(a), use)

    def job_details(self):
        j = self.pages["Jobs"]
        job = j.job() if self.stack.currentWidget() is j else None
        return dialogs.show_job_details(self, job, undo=lambda *a, **k: (self.player.release(), j.undo(*a, **k))[1]) \
            if job else None

    def scripts_target(self):
        """(profile name, plan entry or None) the script editor opens on: the profile shown on
        Profiles, or on Overview the profile the preview uses with the selected album; (None,
        None) elsewhere."""
        page = self.stack.currentWidget() if hasattr(self, "stack") else None
        pr, ov = self.pages.get("Profiles"), self.pages.get("Overview")
        if pr is not None and self.showing("Profiles"):
            name = pr.scope() if pr.list.currentItem() else "global"
            return (name, None) if name and name != "global" else (None, None)
        if ov is not None and page is ov and self.profile_box.currentText():
            return self.profile_box.currentText(), ov.selected()
        return None, None

    def edit_scripts(self):
        name, album = self.scripts_target()
        if name is None:
            return None
        from . import script_editor
        return script_editor.open_editor(self, name, album)

    def update_album_actions(self):
        """Every command is enabled only where it can act: the page shown and its selection."""
        if not hasattr(self, "stack"):
            return
        kind, x = self.album()
        ov = self.pages.get("Overview")
        idle = not getattr(ov, "running", False)
        page = self.stack.currentWidget()
        on_ov = ov is not None and page is ov
        self.commands["add_folder"].setEnabled(idle)            # (no folder picker while a run goes on)
        # Preview and Apply act on the Overview preview: on another page they would act on
        # something not on screen (Watched folders has its own Apply for waiting plans)
        if ov is not None:
            self.profile_box.setEnabled(on_ov and idle)        # (it picks the profile the preview uses)
            for a in getattr(self, "profile_actions", ()):     # (and is not shown where it picks nothing)
                a.setVisible(on_ov)
            self.commands["preview"].setEnabled(on_ov and idle and bool(ov.path))
            self.commands["apply"].setEnabled(on_ov and ov.may_apply_now() and bool(ov.plan) and ov.actionable() > 0)
            self.commands["exclude_again"].setEnabled(kind == "overview" and idle and bool(ov.overridden(x)))
        w = self.pages.get("Watched folders")
        if w is not None:
            on_w = page is w
            folder = on_w and bool(w.selected()[0])
            plan = on_w and w.chosen_plan() is not None and not w.applying
            for k, on in (("change_watch", folder), ("stop_watch", folder), ("process_again", folder and not w.busy),
                          ("show_plan", plan),
                          ("apply_plan", plan), ("dismiss_plan", plan),
                          ("apply_all_plans", on_w and bool(w.plans) and not w.applying)):
                self.commands[k].setEnabled(on)
            self.commands["run_in_background"].setEnabled(w.svc_state is not None and not w.svc_changing)
        j = self.pages.get("Jobs")
        if j is not None:
            job = page is j and bool(j.job()) and not j.working
            self.commands["undo_selected"].setEnabled(job and idle and j.undoable())
            self.commands["show_job_albums"].setEnabled(job)
            self.commands["undo_job"].setEnabled(idle and not j.working)
            self.commands["recover"].setEnabled(idle and not j.working)
        pr = self.pages.get("Profiles")
        if pr is not None:
            on_pr = self.showing("Profiles")
            self.commands["new_rule"].setEnabled(on_pr)
            # (on the rules tab only: Del on the settings tab must not remove a rule out of sight;
            # a global rule shown greyed in a profile is changed under Global rules)
            rule = on_pr and pr.tabs.currentIndex() == 1 and pr.rules.currentRow() >= 0 \
                and pr.rules.selectionModel().hasSelection() and pr.own_rule()
            self.commands["edit_rule"].setEnabled(rule)
            self.commands["delete_rule"].setEnabled(rule)
            t = self.commands["toggle_rule"]
            t.setEnabled(bool(rule))
            t.blockSignals(True)
            t.setChecked(bool(rule) and pr.rule_on())
            t.blockSignals(False)
        on_album_page = any(page is self.pages.get(n) for n in ALBUM_PAGES)
        for k in ("show_inspector", "tab_tags", "tab_artwork", "tab_evidence"):
            self.commands[k].setEnabled(on_album_page)
        for k in ("search_web", "open_folder", "copy_path"):
            self.commands[k].setEnabled(bool(kind) and idle)
        self.commands["use_release"].setEnabled(kind == "overview" and idle)
        search.update_commands(self.commands, ov, kind == "overview" and idle)
        manual_match.update_commands(self.commands, ov, kind == "overview" and idle)
        self.update_m8_actions(kind, x, idle)
        self.commands["file_info"].setEnabled(self.info_file() is not None)
        self.commands["tags_from_filenames"].setEnabled(kind == "overview" and idle and bool(x.get("files"))
                                                        and x.get("status") in planmod.EDITABLE)
        self.commands["job_details"].setEnabled(j is not None and page is j and bool(j.job()))
        self.commands["show_report"].setEnabled(report_mod.can_report(self))
        self.commands["process_anyway"].setEnabled(kind == "overview" and idle and x.get("status") == "excluded")
        self.commands["retry"].setEnabled(kind == "attention" and idle)
        self.inspector.buttons["release"].setEnabled(kind == "overview" and idle)
        for k in ("open", "web"):
            self.inspector.buttons[k].setEnabled(bool(kind))
        on_profiles = self.showing("Profiles")
        self.commands["save_profile"].setEnabled(on_profiles)
        self.commands["tagscript"].setEnabled(on_profiles)
        self.commands["edit_scripts"].setEnabled(self.scripts_target()[0] is not None)
        page = self.stack.currentWidget()
        self.commands["refresh"].setEnabled(any(hasattr(page, m) for m in ("reload", "scan")))
        self.update_tag_actions()
        self.update_edit_actions()

    # ---- the M8 tools: CD lookup, your MusicBrainz account, AcoustID, the player ----------------
    def lookup_cd(self):
        """File > Look Up CD: the sheet for the selected album (or none), on the drive chosen in
        Preferences > Account. Nothing is read or sent until the sheet's own buttons say so."""
        ov = self.pages["Overview"]
        if ov.running:
            self.statusBar().showMessage(tr(self.BUSY), 4000)
            return None
        dlg = browser.open_cd_lookup(ov)
        want = _gui_prefs().get(CD_DRIVE_PREF) or ""
        if want and dlg is not None:
            i = dlg.drive.findData(want)
            if i >= 0:
                dlg.drive.setCurrentIndex(i)
        return dlg

    def overview_album(self):
        """The album selected on Overview (a plan entry), else None (with a word in the status bar)."""
        kind, x = self.album()
        if kind != "overview":
            self.statusBar().showMessage(tr("Select an album in Overview first."), 4000)
            return None
        return x

    def add_to_collection(self):
        a = self.overview_album()
        return account.open_collections(self, a, account.account_of(self)) if a else None

    def submit_ratings(self):
        a = self.overview_album()
        if not a:
            return None
        edit = self.inspector.edit_of(a) if a.get("files") else None
        return account.submit_ratings(self, a, edit, account.account_of(self))

    def submit_acoustids(self):
        a = self.overview_album()
        return acoustid_submit.submit_album(self, a) if a else None

    def update_m8_actions(self, kind, x, idle):
        on = kind == "overview" and idle
        rel = bool(account.release_of(x)) if on else False
        for k in ("add_to_collection", "submit_ratings"):
            self.commands[k].setEnabled(on and rel)
        self.commands["submit_acoustids"].setEnabled(on and acoustid_submit.enabled(x))
        self.commands["lookup_cd"].setEnabled(idle)
        self.update_player_actions()

    def connect_player(self):
        """Space on the album tables plays or pauses the selected album (and in the player bar);
        Controls follows what plays."""
        ov, att = self.pages["Overview"], self.pages["Needs attention"]
        self.player.attach(ov.table, lambda: ov.selected())
        self.player.attach(att.table, lambda: att.folder() if att.table.selectionModel().hasSelection() else None)
        for w in (ov.table, att.table, self.player):
            w.addAction(self.commands["play_pause"])
        self._view_keys = actmod.KeepViewKeys(self)       # (Ctrl+arrows in a table: the table's own keys)
        for v in self.findChildren(QAbstractItemView):
            v.installEventFilter(self._view_keys)
        self.player.playing.connect(lambda _on: self.update_player_actions())
        self.player.track_changed.connect(lambda _p: self.update_player_actions())

    def play_pause(self):
        """Controls > Play/Pause: the album playing pauses or goes on; another selected album plays.
        Without Qt Multimedia a sheet says so, with Open Setup."""
        kind, x = self.album()
        if not self.player.available and setup_mod.needs(self, ("qt_multimedia",), tr("The player is not available.")):
            return
        if kind:
            self.player.play_pause_album(x)
        elif self.player.tracks:
            self.player.toggle()

    def update_player_actions(self):
        if not hasattr(self, "player"):
            return
        kind, _x = self.album()
        has = bool(self.player.tracks)
        # (without Qt Multimedia it stays on: choosing it says what is missing)
        can = self.player.available or self.player_missing()
        self.commands["play_pause"].setEnabled(can and (has or bool(kind)))
        self.commands["next_track"].setEnabled(has and self.player.index < len(self.player.tracks) - 1)
        self.commands["previous_track"].setEnabled(has)
        self.commands["stop_playing"].setEnabled(has or self.player.isVisible())
        for k in ("skip_forward", "skip_back"):           # (disabled, Ctrl+arrows stay the tables' and fields')
            self.commands[k].setEnabled(has and self.player.isVisible())
        for k in ("volume_up", "volume_down", "mute"):
            self.commands[k].setEnabled(self.player.available and self.player.isVisible())

    def player_missing(self) -> bool:
        """Qt Multimedia is not installed (checked once: it cannot appear while the app runs)."""
        if not hasattr(self, "_player_missing"):
            self._player_missing = not setup_mod.rq.ready("qt_multimedia")
        return self._player_missing

    def cancel(self):
        """File > Cancel (Ctrl+.): the work shown on the toolbar's progress bar - a side task's (side_task,
        such as reading an album's fingerprints) when one runs, else Overview's preview or apply."""
        task = getattr(self, "side_task", None)
        if task is not None:
            task.cancel()
        else:
            self.pages["Overview"].cancel()

    def show_progress(self, on):
        """The toolbar's progress bar (a toolbar widget is shown and hidden through its action)."""
        if self.progress_action is not None:
            self.progress_action.setVisible(on)
        if not on:
            self.show_count("")                          # (the next run says its own count)

    def show_count(self, text):
        """The count beside the toolbar's progress bar ("3 of 16"), wide enough for the text."""
        lb = getattr(self, "progress_count", None)
        if lb is None:
            return
        lb.setText(text)
        lb.setMinimumWidth(lb.fontMetrics().horizontalAdvance(text) + 2 if text else 0)
        lb.setVisible(bool(text))

    def refresh(self):
        page = self.stack.currentWidget()
        if hasattr(page, "reload"):
            page.reload()
        elif hasattr(page, "scan"):
            page.scan()

    def add_folder(self):
        self.go("Overview")
        self.pages["Overview"].drop.choose()

    def help(self):
        """The quick start: the copy that came with TagMender, else the one on GitHub."""
        doc = HERE / "docs" / "QUICK_START.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc)) if doc.exists() else
                                 QUrl("https://github.com/herculepirate/tagmender/blob/main/docs/QUICK_START.md"))

    def report(self):
        if sheets.question(self, tr("Report a problem?"),
                           tr("Export Diagnostics… (Preferences > Advanced) saves a zip without keys or tokens to "
                              "attach to the report. The issue tracker opens in your browser."),
                           action=tr("Open Issue Tracker")):
            QDesktopServices.openUrl(QUrl(ISSUES))
            self.preferences().show_tab("Checks")

    def about(self):
        from version import VERSION
        sheets.information(self, tr("TagMender {VERSION}").format(VERSION=VERSION),
                           tr("Identifies, tags and files music with MusicBrainz and Discogs. "
                              "Every change is previewed first and can be undone.\n\nGPL-2.0-or-later. Icons: "
                              "Lucide (ISC)."))

    # ---- drag and drop: a folder anywhere in the window -----------------------
    @staticmethod
    def music(e):
        """The dropped folders and audio files (an image or a web link is not music)."""
        return music_paths(e.mimeData())

    def dropped(self, paths):
        """Folders and audio files dropped: the ones the preview already holds are left out, the
        first new one is previewed, and the status bar says what happened to the others."""
        return manual_match.add_dropped(self, paths)

    BUSY = N_("Finish or cancel the current work first.")

    def dragEnterEvent(self, e):
        """Music is taken anywhere in the window; during a run it is taken too, and the drop
        says why nothing happens (a no-drop cursor alone gives no reason)."""
        if self.music(e):
            e.acceptProposedAction()
            if self.pages["Overview"].running:
                self.statusBar().showMessage(tr(self.BUSY), 4000)
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = self.music(e)
        if not urls:
            return
        e.acceptProposedAction()
        if self.pages["Overview"].running:
            self.statusBar().showMessage(tr(self.BUSY), 4000)
        else:
            self.dropped(urls)

    # ---- the sidebar, pages and search -------------------------------------
    def go(self, name):
        if name in IN_PREFERENCES:                           # (Profiles and Setup: tabs of Preferences)
            self.preferences().show_tab(name)
            return
        self.nav.setCurrentRow(self.nav.row_of(name))

    def showing(self, name) -> bool:
        """The page NAME is the one in view: the window's current page, or for Profiles and Setup,
        Preferences open on its tab."""
        page = self.pages.get(name)
        if name in IN_PREFERENCES:
            p = self.prefs_window
            return page is not None and p is not None and p.isVisible() and p.stack.currentWidget() is page
        return page is not None and hasattr(self, "stack") and self.stack.currentWidget() is page

    def nav_changed(self, row):
        it = self.nav.item(row)
        name = it.data(Qt.UserRole) if it else None
        if not name:
            return
        old = self.stack.currentWidget()
        if hasattr(old, "filter") and self.search_box.text():
            old.filter("")
        page = self.pages[name]
        self.stack.setCurrentWidget(page)
        # (as a Mac window: what it shows in its title - "Overview — TagMender")
        self.setWindowTitle(tr("{page} — {app}").format(page=tr(name), app=APP) + "[*]")
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.search_box.setPlaceholderText(tr(SEARCH_HINT.get(name, N_("Search"))))
        if name == "Overview" and page.selected():
            self.inspector.show_album(page.selected(), page.plan)
        elif name == "Needs attention" and page.row():
            self.inspector.show_row(page.row())
        elif name not in ALBUM_PAGES:
            self.inspector.clear()
        self.place_inspector()
        self.update_album_actions()
        _set_gui_pref("page", name)

    def search(self, text):
        page = self.stack.currentWidget()
        if hasattr(page, "filter"):
            page.filter(text)

    def set_badge(self, name, n):
        self.nav.set_badge(name, n, tr(BADGE_NOUN.get(name, N_("items"))))

    def badge(self, name):
        return self.nav.badge(name)

    def show_run_report(self, rep):
        """The report of the job Apply just ran, as a sheet."""
        return report_mod.open_report(self, rep)

    def refresh_attention_badge(self, path):
        """One rule for the Needs attention badge on every page: the albums marked Unresolved
        under the folder that page looks in (counted on a worker by the page itself)."""
        if path:
            self.pages["Needs attention"].count_under(path)

    # ---- the inspector -------------------------------------------------------
    def album_page(self) -> bool:
        page = self.stack.currentWidget() if hasattr(self, "stack") else None
        return page is None or any(page is self.pages.get(n) for n in ALBUM_PAGES)

    def nothing_to_inspect(self) -> bool:
        """The empty Overview (the drop area, no preview yet): nothing can be selected."""
        ov = self.pages.get("Overview")
        page = self.stack.currentWidget() if hasattr(self, "stack") else None
        return ov is not None and page is ov and ov.views.currentWidget() is ov.drop

    def place_inspector(self):
        """On Overview and Needs attention the inspector is as the person left it; on the other
        pages and the empty Overview (nothing to inspect) it gives the page its room, and comes
        back on return, or once a preview has rows."""
        on = self.inspector_wanted and self.album_page() and not self.nothing_to_inspect()
        if on != self.inspector.isVisible():
            self._placing = True
            try:
                self.inspector.toggle(on) if on else self.inspector.hide()
            finally:
                self._placing = False
            if on:
                QTimer.singleShot(0, self, self.fit_docks)
        a = self.commands["show_inspector"]
        a.blockSignals(True)                                  # (ticked as it is shown: Ctrl+Alt+I on the
        a.setChecked(self.inspector.isVisible() if self.nothing_to_inspect() else self.inspector_wanted)
        a.blockSignals(False)                                 #  empty Overview still opens it)

    def toggle_inspector(self, on):
        if self.album_page():
            self.inspector_wanted = bool(on)
            if on != self.inspector.isVisible():
                self.inspector.toggle(on)

    def inspector_shown(self, on):
        if self._placing or not self.album_page():
            return                                            # (moved aside for a page: not a choice)
        a = self.commands["show_inspector"]
        if a.isChecked() != on and not self.isMinimized():
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)
        if self.isVisible() and not self.isMinimized():
            self.inspector_wanted = bool(on)
            _set_gui_pref("inspector", bool(on))

    def fit_docks(self):
        """With the inspector open the content keeps CONTENT_MIN: the inspector gives way first, and
        grows back to the width the person chose once there is room again."""
        if not self.inspector.isVisible():
            return
        want = int(_gui_prefs().get("inspector_width", style.INSPECTOR[0]))
        room = self.width() - self.nav.width() - CONTENT_MIN
        self._fit_width = max(style.INSPECTOR[1], min(want, room))
        self.resizeDocks([self.inspector], [self._fit_width], Qt.Horizontal)
        QTimer.singleShot(0, self, self.fit_sidebar)

    def fit_sidebar(self):
        """The sidebar gives way next when the content is under CONTENT_MIN, and grows back to its
        chosen width when there is room."""
        if self.inspector.isVisible():
            self._fit_width = self.inspector.width()          # (what the fit gave it, to tell a drag from it)
        if not self.nav.isVisible():
            return
        want = int(_gui_prefs().get("sidebar_width", style.SIDEBAR[0]))
        spare, nav = self.stack.width() - CONTENT_MIN, self.nav.width()
        if spare < 0 and self.inspector.isVisible() and self.inspector.width() > style.INSPECTOR[1]:
            return                                            # (the inspector gives way first: fit_docks)
        if spare < 0 and nav > style.SIDEBAR[1]:
            nav = max(style.SIDEBAR[1], nav + spare)
            nav = max(style.SIDEBAR[1], nav - nav % 8)        # (a step on the 8-pt grid: 216, 208, ...)
        elif spare > 0 and nav < want:
            nav = min(want, nav + spare)
        else:
            return
        self.split.setSizes([nav, self.split.width() - nav])

    def sidebar_dragged(self, *_):
        self._nav_dragged = True                              # (a person's choice: saved on close)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self, self.fit_docks if self.inspector.isVisible() else self.fit_sidebar)

    # ---- Preferences ---------------------------------------------------------
    def preferences(self):
        """Open the Preferences window, or bring it to the front (there is only one)."""
        from .preferences import Preferences
        if self.prefs_window is None:
            self.prefs_window = Preferences(self)
            # (it holds the Profiles and Setup pages: deleted with this window, never left behind)
            self.destroyed.connect(self.prefs_window.deleteLater)
        self.prefs_window.show()
        self.prefs_window.raise_()
        self.prefs_window.activateWindow()
        self.update_album_actions()                          # (Save Profile and the rules, on its Profiles tab)
        return self.prefs_window

    def first_run(self) -> bool:
        """The first start with nothing set up: the window opens on Setup, with a welcome line."""
        return setup_mod.first_run(self)

    # ---- state ---------------------------------------------------------------
    def restore(self):
        d = _gui_prefs()
        self.split.setSizes([int(d.get("sidebar_width", style.SIDEBAR[0])), 900])
        self.go("Overview")                      # (a new session always opens on the Overview)
        tab = d.get("inspector_tab", "Tags")
        self.inspector.tabs.setCurrentIndex(TABS.index(tab) if tab in TABS else 0)
        self.inspector_wanted = bool(d.get("inspector", True))
        self.place_inspector()
        QTimer.singleShot(0, self, self.fit_docks)

    # ---- keyboard: Tab follows reading order ---------------------------------
    @staticmethod
    def _chain(root, inside=None):
        """root's focusable descendants (or the window's, minus `inside`) in focus-chain order."""
        out, w, seen = [], root.nextInFocusChain(), set()
        while w is not None and w is not root and id(w) not in seen:
            seen.add(id(w))
            parent = w.parentWidget()
            if root.isAncestorOf(w) and (w.focusPolicy() & Qt.TabFocus) == Qt.TabFocus \
                    and not (parent is not None and parent.focusProxy() is w) \
                    and not (inside is not None and (w is inside or inside.isAncestorOf(w))):
                out.append(w)
            w = w.nextInFocusChain()
        return out

    def order_focus(self):
        """Toolbar, sidebar, the page shown, then the inspector (it is made first, for the pages
        to use, so by default Tab reached it before the sidebar)."""
        rest = self._chain(self, inside=self.inspector)
        last = rest[-1] if rest else self.nav
        for w in self._chain(self.inspector):
            QWidget.setTabOrder(last, w)
            last = w

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):              # (once: the first time it is shown)
            self._fitted = True
            self.fit_to_screen()

    def fit_to_screen(self):
        """The window as last left (else 1180 × 760), never larger than the screen's free area, so
        every control and the status bar are in view; centred unless a place was kept."""
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        deco_w = max(0, self.frameGeometry().width() - self.geometry().width())
        deco_h = max(0, self.frameGeometry().height() - self.geometry().height()) or 32
        room_w, room_h = scr.width() - deco_w, scr.height() - deco_h
        if room_w < self.minimumWidth() or room_h < self.minimumHeight():   # (a small screen: what fits)
            self.setMinimumSize(min(self.minimumWidth(), room_w), min(self.minimumHeight(), room_h))
        kept = _gui_prefs().get(WINDOW_PREF) or {}
        w = min(int(kept.get("w") or 1180), room_w)
        h = min(int(kept.get("h") or 760), room_h)
        self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
        x, y = kept.get("x"), kept.get("y")
        if not isinstance(x, int) or not isinstance(y, int) or not scr.contains(x + 40, y + 40):
            x = scr.x() + (scr.width() - self.frameGeometry().width()) // 2
            y = scr.y() + (scr.height() - self.frameGeometry().height()) // 2
        x = min(max(x, scr.x()), scr.right() - self.frameGeometry().width() + 1)
        y = min(max(y, scr.y()), scr.bottom() - self.frameGeometry().height() + 1)
        self.move(x, y)
        if kept.get("max"):
            self.showMaximized()

    def save_state(self):
        """Only widths the person dragged to: one squeezed to fit a small window is not kept."""
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        _set_gui_pref(WINDOW_PREF, {"w": g.width(), "h": g.height(), "x": self.x(), "y": self.y(),
                                    "max": bool(self.isMaximized())})
        if self.nav.isVisible() and self._nav_dragged:
            _set_gui_pref("sidebar_width", self.nav.width())
        if self.inspector.isVisible() and self._fit_width is not None \
                and abs(self.inspector.width() - self._fit_width) > 2:
            _set_gui_pref("inspector_width", self.inspector.width())

    def retint(self):
        if "Overview" not in getattr(self, "pages", {}):
            return                                           # (still being built)
        for a in self.commands.values():
            name = a.property("icon_name")
            if name:
                a.setIcon(icons.icon(name, a.property("icon_role") or "ink"))
        self.search_box.actions()[0].setIcon(icons.icon("search", "muted"))
        self.nav.viewport().update()
        self.inspector.retint()
        self.style_release_arrow()
        from .widgets import EmptyState
        for w in self.findChildren(EmptyState):
            w.retint()
        for name in ("Overview", "Needs attention"):
            self.pages[name].folder_icon.setPixmap(icons.icon("folder-open", "muted").pixmap(16, 16))
        self.pages["Watched folders"].retint()
        self.pages["Setup"].retint()
        self.player.retint()

    def threads(self):
        owners = list(self.pages.values()) + [self, self.inspector] + ([self.prefs_window] if self.prefs_window else [])
        # (and a script editor, open or closed with its preview still running: deleted with the window)
        from .script_editor import ScriptEditor
        owners += self.findChildren(ScriptEditor)
        found = [th for o in owners for th, _w in getattr(o, "_threads", []) if _alive(th)]
        # (and every other part's work - the player, Test Connection, the account, the browser
        # button: each thread belongs to a widget inside this window, deleted with it)
        from PySide6.QtCore import QThread
        inside = self.findChildren(QThread) + (self.prefs_window.findChildren(QThread) if self.prefs_window else [])
        return found + [th for th in inside if th not in found and _alive(th)]

    def closeEvent(self, event):
        """Never end the program under a running job or watcher pass (the thread would be
        killed mid-album): ask them to stop, wait, then close."""
        if not getattr(self, "_closing", False) and not self.pages["Profiles"].may_leave():
            event.ignore()                                     # (Cancel on "Save changes to …?")
            return
        if not getattr(self, "_closing", False) and not self.pages["Setup"].may_leave():
            event.ignore()                                     # (Cancel on "Save the keys you typed?")
            return
        self.pages["Watched folders"].finish()
        if self.prefs_window is not None and self.prefs_window.isVisible() and not self.prefs_window.close():
            event.ignore()                                     # (Cancel on "Save the keys you typed?")
            return
        if not self.threads():
            self.save_state()
            self.player.close_player()                         # (its volume saved, the file let go)
            bi = getattr(self, "browser_integration", None)
            if bi is not None:
                bi.stop()
            event.accept()
            return
        if getattr(self, "_closing", False) and time.time() - self._closing_since > 10:
            if sheets.ask(self, tr("TagMender is still finishing an album."),
                          tr("Or it is waiting for the background watcher's pass. An interrupted job is put back as "
                             "it was the next time TagMender starts."),
                          buttons=(tr("Close Anyway"), tr("Keep Waiting")),
                          default=tr("Keep Waiting")) == tr("Close Anyway"):          # (Return keeps waiting)
                event.accept()
                os._exit(0)
        event.ignore()
        self.statusBar().showMessage(tr("Finishing the work in progress before closing…"))
        if not getattr(self, "_closing", False):
            self._closing_since = time.time()
            self._closing = True
            t = QTimer(self)
            t.timeout.connect(lambda: (not self.threads()) and self.close())
            t.start(200)


def main(argv=None):
    os.environ.setdefault("QT_ACCESSIBILITY", "1")
    try:                       # (first: a first run decides between a new and an earlier install)
        import app_settings
        app_settings.load()
    except OSError:
        pass
    try:                       # (the plugins switched on: one that fails is shown in Preferences › Plugins)
        import plugins
        plugins.load_enabled()
    except Exception:
        import logging
        logging.getLogger("tagmender.plugins").exception("plugins were not loaded")
    try:                       # (0.8.6, once: what all profiles agree on becomes shared by them)
        import profiles as _pf
        _pf.adopt_shared()
    except Exception:
        import logging
        logging.getLogger("tagmender.profiles").exception("shared settings not set up")
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP)
    app.setDesktopFileName("io.github.herculepirate.TagMender")
    app.setStyle("Fusion")                                   # (the palette drives every colour)
    i18n.install(app)                                        # (before any window: its words are read once)
    read_desktop_motion()
    global _styled
    _styled = True
    style.apply(app, _gui_prefs().get("appearance", "system"))

    def scheme_changed(*_):
        if _gui_prefs().get("appearance", "system") == "system":
            style.apply(app)
    try:
        app.styleHints().colorSchemeChanged.connect(scheme_changed)
    except AttributeError:
        pass
    win = Main()
    win.show()
    if not win.first_run():                                  # (once, after an upgrade: keep the old built-in?)
        from .profiles import offer_previous_builtin
        QTimer.singleShot(400, win, lambda: offer_previous_builtin(win))
    return app.exec()

