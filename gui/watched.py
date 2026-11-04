"""gui/watched.py - The Watched folders page, and the dialog that adds or changes a watched folder."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy, QSplitter,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import actions, icons, sheets, style
from .common import (STATUS, _fill, _gui_prefs, filter_table, folder_item, heading, path_column, pf,
                     planmod, plural, popup_menu, section, _set_gui_pref, _table, notify, status_cell, watch_does)
from .i18n import N_, tr
from .work import start

# what the watcher did with an album, as the Recently list says it: (word, STATUS colour key, icon)
WATCH_STATUS = {"done": (N_("Done"), "done", "circle-check"),
                "unchanged": (N_("Up to date"), "unchanged", "circle-check"),
                "partial": (N_("Done · Partial"), "done", "circle-check"),
                "planned": (N_("Waiting for approval"), "ready", "circle-dot"),
                "excluded": (N_("Excluded by a rule"), "excluded", "ban"),
                "ignored": (N_("Ignored"), "excluded", "ban"),
                "skipped": (N_("Skipped"), "skipped", "ban"), "undone": (N_("Undone"), "excluded", "undo-2"),
                "unresolved": (N_("Unresolved"), "unresolved", "circle-alert"),
                "provider_error": (N_("Will retry (provider unreachable)"), "unresolved", "circle-alert"),
                "waiting_destination": (N_("Waiting for the library folder"), "unresolved", "circle-alert"),
                "blocked": (N_("Blocked"), "blocked", "circle-x"), "failed": (N_("Failed"), "failed", "circle-x"),
                "error": (N_("Failed"), "failed", "circle-x"),
                "conflict": (N_("Changed since preview"), "conflict", "circle-x")}
# the background service's state in words (watcher.service_status)
SERVICE_WORDS = {"running": N_("Running in the background"), "stopped": N_("Background watcher stopped"),
                 "failed": N_("The background watcher could not start"),
                 "not installed": N_("Not set up to run in the background")}
TABLE_MIN = 2 * (style.ROW + 2) + 36                 # a header and two rows: never one row and a half
FOLDER_ROWS = 4                                      # the folders list grows to this many rows, then scrolls


def watch_word(status):
    word, key, icon = WATCH_STATUS.get(status, (STATUS[status][1] if status in STATUS else
                                                (status or "").replace("_", " ").capitalize(), status, None))
    return tr(word), key, icon


def outcome_text(pairs) -> str:
    """[(album folder, status), ...] in the status words: "2 Done, 1 Failed: Ferry, Garden, Bark"."""
    counts, names = {}, []
    for folder, st in pairs:
        w = watch_word(st)[0]
        counts[w] = counts.get(w, 0) + 1
        names.append(f"{Path(str(folder)).name} ({w.lower()})" if len(set(dict(pairs).values())) > 1
                     else Path(str(folder)).name)
    head = ", ".join(f"{n} {w}" for w, n in counts.items())
    shown = names[:6] + ([f"and {len(names) - 6} more"] if len(names) > 6 else [])
    return f"{head}: {', '.join(shown)}." if names else ""


def watch_root(path, roots):
    """The watched folder an album sits directly in, if any (its name is then left out)."""
    parent = os.path.normpath(os.path.dirname(os.path.abspath(os.path.expanduser(str(path)))))
    return parent if any(os.path.normpath(os.path.abspath(os.path.expanduser(str(r)))) == parent for r in roots) \
        else None


class WatchDialog(QDialog):
    """Add a watched folder, or change which profile one belongs to and how it is handled (a sheet)."""

    def __init__(self, parent, path="", profile=""):
        super().__init__(parent.window() if parent is not None else None)
        sheets.prepare(self, tr("Watched folder"))
        self.resize(600, 0)
        self.old_path = path
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        head = QLabel(tr("Change a watched folder") if path else tr("Watch a folder"))
        head.setProperty("role", "headline")
        lay.addWidget(head)
        form = QFormLayout()
        row = QHBoxLayout()
        self.path = QLineEdit(path)
        self.path.setPlaceholderText(tr("Folder music is dropped into, e.g. ~/Music/Inbox"))
        self.path.setAccessibleName(tr("Folder to watch"))
        browse = QPushButton(tr("Browse…"))
        browse.setAccessibleName(tr("Browse for the folder to watch"))
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.prof = QComboBox()
        self.prof.setAccessibleName(tr("Profile"))
        self.prof.addItems(pf.names())
        if profile:
            self.prof.setCurrentText(profile)
        self.does = QLabel("")
        self.does.setWordWrap(True)
        self.auto = QCheckBox(tr("File albums without asking"))
        self.auto.setToolTip(tr("Off: each album is previewed and waits under \"Waiting for your approval\".\n"
                                "This is a profile setting: it applies to every folder the profile watches."))
        self.auto.setAccessibleName(tr("File albums without asking"))
        self.settle = QDoubleSpinBox()
        self.settle.setAccessibleName(tr("Wait until unchanged for, minutes"))
        self.settle.setRange(0, 1440)
        self.settle.setDecimals(1)
        self.settle.setSuffix(tr(" min"))
        self.settle.setToolTip(tr("An album is left alone until nothing in it changed for this long\n"
                                  "(a copy or download still running)"))
        self.every = QDoubleSpinBox()
        self.every.setAccessibleName(tr("Look every, minutes"))
        self.every.setRange(0.1, 1440)
        self.every.setDecimals(1)
        self.every.setSuffix(tr(" min"))
        self.every.setToolTip(tr("The watcher looks as often as the shortest of all profiles' settings."))
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setProperty("role", "hint")
        form.addRow(tr("Folder"), row)
        form.addRow(tr("Profile"), self.prof)
        form.addRow(tr("What happens"), self.does)
        form.addRow("", self.auto)
        form.addRow(tr("Wait until unchanged for"), self.settle)
        form.addRow(tr("Look every"), self.every)
        lay.addLayout(form)
        every_note = QLabel(tr("These three are saved in the profile. The watcher looks as often as the shortest "
                               "“Look every” of all profiles."))
        every_note.setWordWrap(True)
        every_note.setProperty("role", "hint")
        lay.addWidget(every_note)
        lay.addWidget(self.warn)
        row, _ok = sheets.button_row(self, tr("Watch Folder") if not path else tr("Save Changes"), self.save)
        lay.addLayout(row)                                   # (Cancel, then the default on the right)
        self.here = {}                                       # folder -> is it there (asked on a worker)
        self.asking = None
        self.ask_timer = QTimer(self)                        # (not on every keystroke: a share can hang)
        self.ask_timer.setSingleShot(True)
        self.ask_timer.setInterval(300)
        self.ask_timer.timeout.connect(self.ask_here)
        self.prof.currentTextChanged.connect(self.show_profile)
        self.path.textChanged.connect(lambda _t: (self.show_warnings(), self.ask_timer.start()))
        self.show_profile(self.prof.currentText())
        self.ask_timer.start()

    def folder(self):
        text = self.path.text().strip()
        return Path(os.path.abspath(os.path.expanduser(text))) if text else None

    def ask_here(self):
        """Is the folder there? Asked on a worker (an unmounted share or a dead network mount can
        take seconds to answer); the answer is dropped when the text has changed since."""
        folder = self.folder()
        if folder is None or folder in self.here or self.asking == folder:
            return
        self.asking = folder
        owner = self.parentWidget() or self                  # (outlives the sheet: its thread is waited for)

        def done(here):
            try:
                self.here[folder] = here
                if self.asking == folder:
                    self.asking = None
                if self.folder() == folder:
                    self.show_warnings()
            except RuntimeError:                             # (the sheet was closed and deleted)
                pass
        start(owner, lambda _w: folder.is_dir(), done, failed=lambda _m: None)

    def browse(self):
        d = sheets.choose_folder(self, tr("Choose the folder to watch"), self.path.text() or str(Path.home()))
        if d:
            self.path.setText(d)

    def profile(self):
        try:
            return pf.load(self.prof.currentText())
        except pf.ProfileError:
            return None

    def show_profile(self, _name):
        p = self.profile()
        if p is None:
            self.does.setText("")
            return
        self.does.setText(watch_does(p))
        self.auto.setChecked(bool(p["unattended"]))
        self.settle.setValue(p["watch"]["settle_minutes"])
        self.every.setValue(p["watch"]["interval_seconds"] / 60)
        self.show_warnings()

    def problems(self):
        """(errors that stop saving, notes)"""
        p = self.profile()
        text = self.path.text().strip()
        errs, notes = [], []
        if not text:
            errs.append(tr("Choose the folder to watch."))
        if p is None:
            errs.append(tr("Create a profile first (Profiles)."))
            return errs, notes
        folder = Path(os.path.abspath(os.path.expanduser(text))) if text else None
        if folder is not None and self.here.get(folder) is False:      # (asked on a worker: ask_here)
            notes.append(tr("This folder is not there now (not mounted?). It is watched once it is."))
        if p["operation"] == "in_place":
            notes.append(tr("This profile fixes albums where they are, so dropped albums stay in this folder. "
                            "To file them into your library, choose a profile that moves or copies."))
        own = (p.source_destination(text) if text else "") or p["destination"]   # (this folder's own library)
        dest = Path(os.path.abspath(os.path.expanduser(own))) if own else None
        if folder is not None and dest is not None and p["operation"] != "in_place":
            if dest == folder:
                notes.append(tr("This folder IS the profile's library: every album already in it will be "
                                "checked and filed again by this profile."))
            elif folder in dest.parents:
                notes.append(tr("The library is inside this folder: what is in it is left alone, only new "
                                "drops are filed."))
            elif dest in folder.parents:
                notes.append(tr("This folder is inside the profile's library."))
        if folder is not None:
            for n in pf.names():
                if n == p.name:
                    continue
                try:
                    q = pf.load(n)
                except pf.ProfileError:
                    continue
                for s in q["sources"]:
                    if s.get("watch") and os.path.abspath(os.path.expanduser(s["path"])) == str(folder) \
                            and str(folder) != os.path.abspath(os.path.expanduser(self.old_path or "-")):
                        notes.append(tr("Profile \"{n}\" watches this folder now; saving moves it to "
                                        "\"{name}\".").format(n=n, name=p.name))
        if not self.auto.isChecked():
            notes.append(tr("Albums are previewed and wait for your approval on this page."))
        return errs, notes

    def show_warnings(self):
        errs, notes = self.problems()
        self.warn.setText("\n".join(errs + notes))

    def save(self):
        errs, _notes = self.problems()
        if errs:
            sheets.information(self, errs[0], "\n".join(errs[1:]))
            return
        folder = os.path.abspath(os.path.expanduser(self.path.text().strip()))
        old = os.path.abspath(os.path.expanduser(self.old_path)) if self.old_path else None
        same = lambda s: os.path.abspath(os.path.expanduser(s.get("path", ""))) in {folder, old}  # noqa: E731
        try:
            # the new owner first, checked: a bad setting changes no profile
            data = self.profile().to_dict()
            had = [s for s in data["sources"] if same(s)]
            data["sources"] = [s for s in data["sources"] if not same(s)] + [
                dict(had[0] if had else {"recursive": True}, path=folder, watch=True)]
            data["unattended"] = self.auto.isChecked()
            data["watch"] = dict(data.get("watch") or {}, settle_minutes=self.settle.value(),
                                 interval_seconds=max(1, int(round(self.every.value() * 60))))
            target = pf.from_dict(data)
            others = []
            for n in pf.names():                     # a folder belongs to one profile
                if n == target.name:
                    continue
                try:
                    q = pf.load(n)
                except pf.ProfileError:
                    continue
                if any(s.get("watch") and same(s) for s in q["sources"]):
                    d = q.to_dict()
                    d["sources"] = [s for s in d["sources"] if not (s.get("watch") and same(s))]
                    others.append(pf.from_dict(d))
            pf.save(target)
            for o in others:
                pf.save(o)
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The folder was not saved."), str(exc))
            return
        self.accept()


class Watched(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.busy = False
        self.applying = False                                  # waiting plans being applied (on a worker)
        self.watcher = None
        self.plans, self.prow_plans = [], []
        self.svc_state = None
        outer = QVBoxLayout(self)
        outer.addLayout(heading(tr("Watched folders"), tr("Music dropped into these folders is identified, tagged and "
                                                   "filed by the folder's profile once it has settled.")))
        # three lists, one above the other, each scrolling on its own (no page scroll around them)
        split = QSplitter(Qt.Vertical)
        split.setObjectName("pageSplit")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        outer.addWidget(split, 1)

        def pane():
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 2)
            v.setSpacing(6)
            split.addWidget(w)
            return v
        lay = pane()
        self.svc_changing = False                              # (the service being installed or stopped)
        self.table = _table([N_("Folder"), N_("Profile"), N_("What happens"), N_("Applies"), N_("Available")],
                            stretch=2,
                            fit=(1, 3, 4))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().resizeSection(0, 180)
        path_column(self.table, 0)                             # (a whole path: its start and its end in view)
        self.table.activated.connect(lambda *_: self.change())  # (Return, or a double click)
        self.table.setMinimumHeight(TABLE_MIN)
        self.table.setAccessibleName(tr("Watched folders"))
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.menu_for(self.table, ("change_watch", "stop_watch", None, "check_now"))
        lay.addWidget(self.table, 1)
        # a list editor's + and − under the list, as on a Mac (Change… and Check Now: the context
        # menu, Return and the Tools menu)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.add_btn, self.stop_btn = (self.edit_button(icon, tip, fn) for icon, tip, fn in (
            ("plus", "Watch a Folder…", self.add), ("minus", "Stop Watching the Selected Folder…", self.remove)))
        row.addWidget(self.add_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.live = QCheckBox(tr("Keep watching while TagMender is open"))
        self.live.setAccessibleName(tr("Keep watching while TagMender is open"))
        self.live.setChecked(bool(_gui_prefs().get("watch_in_app")))
        self.live.toggled.connect(self.set_live)
        row.addWidget(self.live)
        row.addStretch(1)
        self.svc_label = QLabel("")
        self.svc_label.setProperty("role", "hint")
        self.svc_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # (it gives way at 960 px)
        self.svc_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.svc_btn = QPushButton(tr("Run in the Background…"))
        self.svc_btn.setAccessibleName(tr("Run in the background"))
        self.svc_btn.setToolTip(tr("A systemd user service that keeps watching when TagMender is closed"))
        self.svc_btn.clicked.connect(self.toggle_service)
        self.svc_btn.hide()                                    # (until the service's state is known)
        row.addWidget(self.svc_label)
        row.addWidget(self.svc_btn)
        lay.addLayout(row)
        lay = pane()
        lay.addLayout(section(tr("Waiting for your approval")))
        self.pending = _table([N_("Album"), N_("Filed into"), N_("Profile")], stretch=1, fit=(2,))
        self.pending.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.pending.horizontalHeader().resizeSection(0, 260)
        path_column(self.pending, 1)
        self.pending.activated.connect(lambda *_: self.show_plan())
        self.pending.setMinimumHeight(TABLE_MIN)
        self.pending.setAccessibleName(tr("Albums waiting for your approval"))
        self.pending.itemSelectionChanged.connect(self.update_buttons)
        self.menu_for(self.pending, ("show_plan", "apply_plan", "dismiss_plan"))
        lay.addWidget(self.pending, 1)
        prow = QHBoxLayout()                                  # one action: each album's own are in its
        self.plan_btns = {}                                   # context menu and the Tools menu
        b = QPushButton(tr("Apply All…"))
        b.setProperty("role", "primary")
        b.setAccessibleName(tr("Apply all waiting albums"))
        b.setToolTip(tr("Apply the changes to every album waiting for your approval"))
        b.clicked.connect(self.apply_all)
        prow.addStretch(1)
        prow.addWidget(b)
        self.plan_btns["all"] = b
        lay.addLayout(prow)
        lay = pane()
        self.recent_pane = lay.parentWidget()
        lay.addWidget(self.hairline())
        self.fold = QToolButton()                              # "Recently" folds away on a short window
        self.fold.setProperty("role", "tool")
        self.fold.setText(tr("Recently"))
        self.fold.setCheckable(True)
        self.fold.setChecked(True)
        self.fold.setArrowType(Qt.DownArrow)
        self.fold.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.fold.setAccessibleName(tr("Show what was handled recently"))
        f = self.fold.font()
        f.setWeight(f.Weight.DemiBold)
        self.fold.setFont(f)
        self.fold.toggled.connect(self.show_recent)
        self.fold_chosen = False                               # (the person opened or closed it)
        self.fold.clicked.connect(lambda: setattr(self, "fold_chosen", True))
        lay.addWidget(self.fold, 0, Qt.AlignLeft)
        self.recent = _table([N_("When"), N_("Album"), N_("Outcome"), N_("Profile")], stretch=1, fit=(0, 2, 3))
        self.recent.setMinimumHeight(TABLE_MIN)
        self.recent.setAccessibleName(tr("Recently handled albums"))
        lay.addWidget(self.recent, 1)
        self.out = QLabel("")
        self.out.setWordWrap(True)
        self.out.setProperty("role", "hint")
        self.out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.out)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 2)
        self.split = split
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_now)
        self.reload()
        self.set_live(self.live.isChecked(), save=False)

    def edit_button(self, icon, tip, fn):
        b = QToolButton()
        b.setProperty("role", "tool")
        b.setIcon(icons.icon(icon, "ink"))
        b.setToolTip(tip)
        b.setAccessibleName(tip.rstrip("…"))
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.StrongFocus)
        b.clicked.connect(fn)
        b.icon_name = icon
        return b

    def retint(self):
        for b in (self.add_btn, self.stop_btn):
            b.setIcon(icons.icon(b.icon_name, "ink"))

    def fit_top(self):
        """The folders list as tall as its rows (two to FOLDER_ROWS), not a band of empty rows."""
        n = max(2, min(FOLDER_ROWS, self.table.rowCount()))
        h = n * (style.ROW + 2) + 36
        self.table.setMaximumHeight(h)
        pane = self.table.parentWidget()
        pane.setMaximumHeight(pane.layout().minimumSize().height() - self.table.minimumHeight() + h)

    @staticmethod
    def hairline():
        from .common import separator
        return separator()

    # page heights under which "Recently" folds away (unless the person opened it): the two lists
    # above keep two whole rows each at 960 x 600
    FOLD_UNDER = 680

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.fold_chosen:
            want = self.window().height() >= self.FOLD_UNDER
            if self.fold.isChecked() != want:
                self.fold.setChecked(want)

    def show_recent(self, on):
        self.fold.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.recent.setVisible(on)
        self.recent_pane.setMaximumHeight(16777215 if on else self.recent_pane.sizeHint().height())

    def menu_for(self, table, keys):
        """The table's context menu: the same commands as the Tools menu (the Menu key too)."""
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos: popup_menu(self, table, pos, [self.win.act(k) for k in keys]))

    def update_buttons(self):
        """Each button is enabled only when it can act (a selection; nothing being applied)."""
        folder = bool(self.selected()[0])
        self.stop_btn.setEnabled(folder)
        self.plan_btns["all"].setEnabled(bool(self.plans) and not self.applying)
        self.win.update_album_actions()

    # ---- the list ----------------------------------------------------------
    def rows(self):
        out = []
        for n in pf.names():
            try:
                p = pf.load(n)
            except pf.ProfileError:
                continue
            out += [(s["path"], p) for s in p["sources"] if s.get("watch")]
        return out

    def reload(self):
        """The lists from the profiles and TagMender's own state; whether each folder is there
        and the background service's state are asked on a worker (an unmounted share can hang
        for seconds, and systemctl is a process)."""
        rows = self.rows()
        _fill(self.table, [(path, p.name, watch_does(p, path), tr("Automatically") if p["unattended"] else tr("Asks first"),
                            "…") for path, p in rows])
        for r, (path, _p) in enumerate(rows):
            self.table.item(r, 0).setToolTip(path)
        self.fit_top()
        roots = [path for path, _p in rows]
        self.plans = []
        try:
            import watcher
            self.plans = watcher.pending_plans()
            prows = []
            for x in self.plans:
                for src, label, to in x["albums"] or [(tr("(changes that cannot be read)"), "", "")]:
                    if to and os.path.normpath(to) == os.path.normpath(src):
                        to = tr("stays where it is ({0})").format(label.lower() or 'tagged')
                    prows.append((src, to, x["profile"], x))
            self.prow_plans = [r[3] for r in prows]
            _fill(self.pending, [r[:3] for r in prows])
            for r, (src, to, _pn, _x) in enumerate(prows):
                self.pending.setItem(r, 0, folder_item(src, album=True, root=watch_root(src, roots)))
                self.pending.item(r, 1).setToolTip(to)
        except Exception:                                    # the page still shows the folders
            self.plans, self.prow_plans = [], []
            self.pending.setRowCount(0)
        try:
            import watcher
            recent = watcher.recent(40)
            _fill(self.recent, [(time.strftime("%Y-%m-%d %H:%M", time.localtime(ts or 0)), f, st, pn)
                                for f, pn, st, ts, _l in recent])
            for r, (f, _pn, st, _ts, _l) in enumerate(recent):
                self.recent.setItem(r, 1, folder_item(f, album=True, root=watch_root(f, roots)))
                word, colour, icon = watch_word(st)
                self.recent.setItem(r, 2, status_cell(QTableWidgetItem(), colour, word, icon))
        except Exception:
            self.recent.setRowCount(0)
        self.win.set_badge("Watched folders", len(self.prow_plans))     # (albums waiting, as the list rows)
        if getattr(self, "timer", None) is not None and self.timer.isActive():
            ms = self.interval_ms()
            if ms != self.timer.interval():                  # "Look every" changed
                self.timer.start(ms)
        self.update_buttons()
        self.ask_slow([path for path, _p in rows])

    def ask_slow(self, paths):
        def work(_w):
            here = [Path(p).expanduser().is_dir() for p in paths]
            st = None
            try:
                import watcher
                if watcher.service_supported():
                    st = watcher.service_status()
            except Exception:
                st = None
            return paths, here, st

        def done(out):
            paths_, here, st = out
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 0)
                if it is not None and r < len(paths_) and it.text() == paths_[r]:
                    status_cell(self.table.item(r, 4), "done" if here[r] else "failed",
                                tr("Yes") if here[r] else tr("Not mounted"), "circle-check" if here[r] else "circle-x")
            self.show_service(st)
        start(self, work, done, failed=lambda _m: None)

    def show_service(self, st):
        self.svc_state = st
        self.svc_changing = False
        a = self.win.act("run_in_background") if self.win is not None else None
        if st is None:
            self.svc_label.setText("")
            self.svc_btn.hide()
            if self.win is not None:
                self.win.update_album_actions()
            return
        self.svc_label.setText(tr(SERVICE_WORDS.get(st, st.capitalize())))
        text = tr("Stop the Background Watcher…") if st == "running" else tr("Run in the Background…")
        self.svc_btn.setText(text)
        self.svc_btn.setAccessibleName(text.rstrip("…"))
        self.svc_btn.setEnabled(True)
        self.svc_btn.show()
        if a is not None:                                      # (the Tools menu says the same)
            actions.retitle(a, tr("Stop the &Background Watcher…") if st == "running" else
                            tr("Run Watcher in the &Background…"))
            self.win.update_album_actions()

    def selected(self):
        r = self.table.currentRow()
        if r < 0 or self.table.item(r, 0) is None or not self.table.selectionModel().hasSelection():
            return None, None
        return self.table.item(r, 0).text(), self.table.item(r, 1).text()

    def add(self):
        if not pf.names():
            sheets.information(self, tr("Create a profile first."), tr("A watched folder belongs to a profile: it says "
                               "how the music dropped there is tagged and filed. File > New Profile… (Ctrl+N)."))
            return
        if sheets.run(WatchDialog(self), "watch"):
            self.reload()

    def change(self):
        path, name = self.selected()
        if path and sheets.run(WatchDialog(self, path, name), "watch"):
            self.reload()

    def filter(self, text):
        for t in (self.table, self.pending, self.recent):
            filter_table(t, text)

    def remove(self):
        path, name = self.selected()
        if not path:
            return
        if not sheets.confirm(self, tr("Stop watching this folder?"),
                              tr("{path} is no longer watched by the profile “{name}”. The folder and the music "
                                 "in it are left as they are.").format(path=path, name=name),
                              tr("To watch it again, choose File > Watch a Folder…"), tr("Stop Watching"),
                              destructive=True):
            return
        try:
            data = pf.load(name).to_dict()
            data["sources"] = [s for s in data["sources"] if not (s.get("watch") and s.get("path") == path)]
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            sheets.warning(self, tr("The folder is still watched."), str(exc))
        self.reload()

    # ---- watching ------------------------------------------------------------
    def set_live(self, on, save=True):
        if on and save and not self.ask_live():
            for box in (self.live, getattr(getattr(getattr(self.win, "prefs_window", None), "general", None),
                                           "watch", None)):
                if box is not None:                          # (Cancel: both boxes unticked, quietly)
                    box.blockSignals(True)
                    box.setChecked(False)
                    box.blockSignals(False)
            return
        if save:
            _set_gui_pref("watch_in_app", bool(on))
        if not on:
            self.timer.stop()
            return
        self.timer.start(self.interval_ms())
        self.check_now()

    def ask_live(self) -> bool:
        """Watching while open files albums with no sheet for a profile set to Automatically:
        that is asked first, as Run in the Background is."""
        rows = self.rows()
        auto = [path for path, p in rows if p["unattended"]]
        if not auto:
            return True
        names = ", ".join(Path(a).name or a for a in auto[:3]) + (f" and {len(auto) - 3} more" if len(auto) > 3 else "")
        mins = max(1, round(self.interval_ms() / 60000))
        return sheets.confirm(self, tr("Keep watching while TagMender is open?"),
                              tr("{0} {1} checked "
                                 "every {mins} min; albums in {names} are filed automatically, without asking.")
                              .format(plural(len(rows), tr('watched folder'), tr('watched folders')),
                                      'is' if len(rows) == 1 else 'are', mins=mins, names=names),
                              tr("Every job can be undone from Jobs; untick this to stop."), tr("Start Watching"))

    def say(self, text):
        """An outcome on the page and in the status bar (at 960 x 600 "Recently" is folded away)."""
        self.out.setText(text)
        if self.win is not None and text:
            self.win.statusBar().showMessage(text.splitlines()[0], 8000)

    def interval_ms(self) -> int:
        secs = [p["watch"]["interval_seconds"] for _path, p in self.rows()] or [300]
        return int(max(5, min(secs)) * 1000)

    def finish(self) -> bool:
        """Before the window closes: no new pass, the running one asked to stop after the
        album it is on. True when nothing is running any more."""
        self.timer.stop()
        if self.watcher is not None:
            self.watcher.stop = True
        return not self.busy

    def check_now(self):
        if self.busy or not self.rows():
            if not self.rows():
                self.out.setText(tr("No folder is watched yet. Choose Watch a Folder… to add one."))
            return
        self.busy = True

        def work(_w):
            import watcher
            w = watcher.Watcher(once=True)
            self.watcher = w
            res = w.tick()
            return res, getattr(w, "skipped", "")
        self.out.setText(tr("Checking…"))

        def done(out):
            res, skipped = out
            self.busy = False
            self.watcher = None
            self.say(tr("Skipped: {skipped}.").format(skipped=skipped) if skipped else
                     outcome_text(list(res.items())) or
                     tr("Nothing new or changed in the watched folders ({0}).").format(time.strftime('%H:%M')))
            self.reload()
            if res:
                self.win.pages["Jobs"].reload()
                notify(self.win, "watch", tr("Watched folders"), outcome_text(list(res.items())))

        def failed(msg):
            self.busy = False
            self.watcher = None
            self.say(tr("The check failed: {msg}").format(msg=msg))
        start(self, work, done, failed=failed)

    def toggle_service(self):
        """Install or remove the systemd user service, on a worker (systemctl can take a while)."""
        running = self.svc_state == "running"
        if self.svc_state is None or self.svc_changing:
            return
        if not running:
            if not self.rows():
                sheets.information(self, tr("Watch a folder first."),
                                   tr("The background service watches the folders listed on this page."))
                return
            if not sheets.confirm(self, tr("Keep watching when TagMender is closed?"),
                                  tr("A systemd user service (tagmender-watch) is installed and started: it checks the "
                                     "folders listed here, as their profiles say, also after you log in again."),
                                  tr("To stop it, come back here and choose Stop the Background Watcher…"),
                                  tr("Run in the Background")):
                return

        def work(_w):
            import watcher
            return watcher.stop_service() if running else watcher.install_service()

        def done(msg):
            self.svc_changing = False
            if msg not in ("running", "stopped"):
                sheets.warning(self, tr("The background watcher did not change."), msg)
            elif self.win is not None:
                self.win.statusBar().showMessage(tr(SERVICE_WORDS.get(msg, msg)) + ".", 6000)
            self.reload()

        def failed(msg):
            self.svc_changing = False
            sheets.warning(self, tr("The background watcher did not change."), msg)
            self.reload()
        self.svc_changing = True
        self.svc_btn.setEnabled(False)
        if self.win is not None:
            self.win.update_album_actions()
        self.svc_label.setText(tr("Stopping…") if running else tr("Starting…"))
        start(self, work, done, failed=failed)

    # ---- plans waiting for approval -----------------------------------------
    def chosen_plan(self):
        r = self.pending.currentRow()
        if not self.pending.selectionModel().hasSelection():
            return None
        plans = getattr(self, "prow_plans", [])
        return plans[r] if 0 <= r < len(plans) else None

    @staticmethod
    def plan_name(x) -> str:
        """The album a waiting plan is for ("Ferry"), or "3 albums"."""
        albums = x["albums"] or []
        return Path(albums[0][0]).name if len(albums) == 1 else plural(len(albums), tr("album"), tr("albums"))

    def show_plan(self):
        x = self.chosen_plan()
        if not x:
            return
        try:
            text = planmod.preview(planmod.load(x["path"]))
        except Exception as exc:
            text = tr("These changes cannot be read: {exc}").format(exc=exc)
        sheets.show_text(self, tr("What changes for {0}").format(self.plan_name(x)), text,
                         tr("What applying changes for it. Nothing has "
                            "changed yet. (Job {0}, profile {1})").format(x['job'], x.get('profile') or '?'))

    def _apply(self, plans):
        plans = [x for x in plans if not x.get("error")]
        if not plans or self.applying:
            return

        def work(_w):
            import watcher
            out, errors = [], []
            for x in plans:
                try:
                    res = watcher.apply_pending(x["path"])
                    out += [(r["album"], r["status"]) for r in res["albums"]]
                except Exception as exc:
                    errors.append(tr("Not applied: {name}: {exc}")
                                  .format(name=Path((x['albums'] or [[x['job']]])[0][0]).name, exc=exc))
            return [outcome_text(out)] + errors if out else errors

        def finished():
            self.applying = False
            self.reload()
            self.win.pages["Jobs"].reload()

        def done(lines):
            self.say("\n".join(lines))
            finished()

        def failed(msg):
            self.say(tr("Not applied: {0}").format(msg.splitlines()[0] if msg else ''))
            finished()
        self.applying = True                                  # (no second Apply on the same plan)
        self.update_buttons()
        self.out.setText(tr("Applying…"))
        start(self, work, done, failed=failed)

    def apply_one(self):
        x = self.chosen_plan()
        if not x or x.get("error"):
            return
        albums = x["albums"] or []
        name = self.plan_name(x)
        to = {os.path.dirname(t) for _s, _l, t in albums if t}
        where = tr("filed into {0}").format(sorted(to)[0]) if len(to) == 1 else "filed"
        if sheets.confirm(self, tr("Apply the waiting changes to {name}?").format(name=name),
                          tr("{0} tagged and {where}, as Show Changes lists.").format(plural(len(albums), tr('album'),
                                                                                             tr('albums')),
                                                                                      where=where),
                          tr("Every job can be undone from Jobs."), tr("Apply Changes")):
            self._apply([x])

    def apply_all(self):
        plans = [x for x in self.plans if not x.get("error")]
        if not plans or self.applying:
            return
        n = sum(len(x["albums"] or []) for x in plans)
        if sheets.confirm(self, tr("Apply the changes to all {0}?").format(plural(n, tr('waiting album'),
                                                                                  tr('waiting albums'))),
                          tr("{0} tagged and filed, as Show Changes lists for each.").format(plural(n, tr('album'),
                                                                                                    tr('albums'))),
                          tr("Every job can be undone from Jobs."),
                          tr("Apply {0}").format(plural(n, tr('Album'), tr('Albums')))):
            self._apply(plans)

    def dismiss(self):
        x = self.chosen_plan()
        if not x or self.applying:
            return
        name = self.plan_name(x)
        if not sheets.confirm(self, tr("Dismiss the waiting changes for {name}?").format(name=name),
                              tr("{name} stays as it is and its waiting changes are dropped.").format(name=name),
                              tr("Check Now finds it again while it is in a watched folder."), tr("Dismiss"),
                              destructive=True):
            return
        import watcher
        try:
            watcher.dismiss_pending(x["path"])
        except OSError as exc:
            sheets.warning(self, tr("The waiting changes were not dismissed."), str(exc))
        self.reload()
