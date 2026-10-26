"""gui.py - TagMender's desktop app (PySide6). The same engine, planner, executor, profiles
and rules as the CLI; work runs on a background thread so the window never freezes.

  Overview          drop a folder (or choose one), pick a profile, Preview, then Apply
  Jobs              what ran; undo a job, recover an interrupted one
  Watched folders   the folders profiles keep processed; check them now
  Profiles          settings per profile, and its Ignore/Skip rules with a rule tester
  Needs attention   albums marked Unresolved, by reason; retry, search the web, open
  History           what rules excluded, and per-job album results
  Settings          provider keys (entered, never shown), checks, diagnostics export

Keyboard: Ctrl+O choose folder, Ctrl+P preview, Ctrl+Return apply, Esc cancel,
Ctrl+F search, Alt+1..7 pages. Status is always shown as a symbol and a word, never by
colour alone; light/dark follows the desktop.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QRectF, QSize, QSortFilterProxyModel, Qt,
                            QThread, QUrl, Signal, Slot)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget,
                               QStatusBar, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import plan as planmod  # noqa: E402
import profiles as pf  # noqa: E402
import reasons  # noqa: E402

APP = "TagMender"

# status -> (symbol, word, light colour, dark colour)
STATUS = {
    "ready": ("●", "Ready", "#0F7C80", "#4FB3B5"),
    "done": ("✓", "Done", "#2E7D4F", "#6CC08E"),
    "unchanged": ("✓", "Up to date", "#2E7D4F", "#6CC08E"),
    "unresolved": ("!", "Unresolved", "#B7791F", "#E3B25C"),
    "excluded": ("⊘", "Excluded", "#6B5B95", "#A99AD6"),
    "skipped": ("⊘", "Skipped", "#6B5B95", "#A99AD6"),
    "blocked": ("✕", "Blocked", "#B3261E", "#F08A80"),
    "failed": ("✕", "Failed", "#B3261E", "#F08A80"),
    "conflict": ("✕", "Changed since preview", "#B3261E", "#F08A80"),
}


def is_dark():
    try:
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return QApplication.palette().color(QPalette.Window).lightness() < 128


def status_color(key):
    s = STATUS.get(key)
    return QColor((s[3] if is_dark() else s[2]) if s else ("#9AA5B1" if is_dark() else "#5D6B7A"))


def label_for(album):
    """'Ignored' / 'Skipped' for excluded albums (never 'Unresolved'), else the status word."""
    st = album["status"]
    if st in ("excluded", "skipped") and album.get("label"):
        return STATUS[st][0], album["label"]
    sym, word = STATUS.get(st, ("·", st.title()))[:2]
    return sym, word


def stylesheet():
    dark = is_dark()
    accent = "#4FB3B5" if dark else "#0F7C80"
    side = "#1C222A" if dark else "#EEF0F3"
    ink = "#E6EAF0" if dark else "#1C2430"
    muted = "#9AA5B1" if dark else "#5D6B7A"
    faint = "#4A535E" if dark else "#B9C1CA"
    return f"""
    QListWidget#nav {{ background: {side}; border: none; padding: 8px 6px; outline: 0; }}
    QListWidget#nav::item {{ padding: 6px 10px; margin: 1px 0; min-height: 22px; border-radius: 6px;
                             color: {ink}; }}
    QListWidget#nav::item:selected {{ background: {accent}; color: white; }}
    QListWidget#nav::item:focus {{ border: 2px solid {accent}; }}
    QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; }}
    QLabel[role="hint"] {{ color: {muted}; }}
    QPushButton[role="primary"] {{ background: {accent}; color: white; border: none; border-radius: 6px;
                                  padding: 6px 14px; font-weight: 600; }}
    QPushButton[role="primary"]:disabled {{ background: transparent; color: {faint};
                                           border: 1px solid {faint}; }}
    *:focus {{ outline: 2px solid {accent}; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus, QTableView:focus, QCheckBox:focus {{
        border: 2px solid {accent}; border-radius: 6px; }}
    """


# --------------------------------------------------------------------------
# background work
# --------------------------------------------------------------------------
class Worker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancelled = False

    def run(self):
        try:
            out = self.fn(self)
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=3)}")
            return
        self.finished.emit(out)


def _alive(th) -> bool:
    try:
        return th.isRunning()
    except RuntimeError:                      # (deleted once it finished)
        return False


class _Relay(QObject):
    """Calls fn on the thread it lives on (the UI thread): a plain Python function connected
    to a worker's signal would otherwise run on the worker's thread, where no widget may be
    touched."""
    def __init__(self, parent, fn):
        super().__init__(parent)
        self.fn = fn

    @Slot(object)
    def one(self, x):
        self.fn(x)

    @Slot(int, int, str)
    def three(self, a, b, c):
        self.fn(a, b, c)


def start(owner, fn, done, progress=None, failed=None):
    """Run fn(worker) on a thread; done(result), failed(message) and progress(...) on the UI
    thread."""
    th = QThread(owner)
    w = Worker(fn)
    w.moveToThread(th)
    th.started.connect(w.run)
    relays = [_Relay(owner, done), _Relay(owner, failed or (lambda msg: QMessageBox.warning(owner, APP, msg)))]
    w.finished.connect(relays[0].one)
    w.failed.connect(relays[1].one)
    if progress:
        relays.append(_Relay(owner, progress))
        w.progress.connect(relays[2].three)
    w._relays = relays                                  # (kept alive with the worker)
    for sig in (w.finished, w.failed):
        sig.connect(th.quit)
    th.finished.connect(th.deleteLater)
    for r in relays:                                    # (after the result was handled: queued in order)
        th.finished.connect(r.deleteLater)
    owner._threads = [(t, x) for t, x in getattr(owner, "_threads", []) if _alive(t)] + [(th, w)]
    th.start()
    return w


# --------------------------------------------------------------------------
# the plan as a table
# --------------------------------------------------------------------------
class PlanModel(QAbstractTableModel):
    COLS = ("Status", "Album folder", "Source", "Score", "Changes", "Why")

    def __init__(self):
        super().__init__()
        self.albums = []

    def set_plan(self, plan):
        self.beginResetModel()
        self.albums = plan["albums"] if plan else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.albums)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLS[s]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        a = self.albums[idx.row()]
        c = idx.column()
        ident = a["identification"]
        if role == Qt.DisplayRole:
            if c == 0:
                sym, word = label_for(a)
                return f"{sym}  {word}"
            if c == 1:
                return Path(a["source_dir"]).name + "   —   " + Path(a["source_dir"]).parent.name
            if c == 2:
                return (ident.get("provider") or "").replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")
            if c == 3:
                return f"{ident['confidence']:.0f}" if ident.get("confidence") else ""
            if c == 4:
                n = sum(1 for fe in a["files"] if fe["op"] != "none")
                moves = sum(1 for fe in a["files"] if fe["op"] in ("move", "copy"))
                return (f"{n} file(s)" + (f", {moves} {a['files'][0]['op']}" if moves else "")) if n else ""
            if c == 5:
                if a["status"] in ("excluded",):
                    return f"rule “{a['rule']['name']}”"
                if a["conflicts"]:
                    return a["conflicts"][0]
                if ident.get("status") == "unresolved":
                    codes, words = reasons.parse(ident.get("reason", ""))
                    return ", ".join(codes)
                return "; ".join(ident.get("why") or [])[:120]
        if role == Qt.ForegroundRole and c == 0:
            return status_color(a["status"])
        if role == Qt.ToolTipRole and c == 1:
            return a["source_dir"]
        if role == Qt.AccessibleTextRole and c == 0:
            return label_for(a)[1]
        if role == Qt.UserRole:
            return a
        return None


class SleeveDrop(QFrame):
    """The empty state: drop a folder on an empty record sleeve, or click to choose one."""
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(170)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Drop a music folder here, or press Enter to choose one")
        self.setCursor(Qt.PointingHandCursor)
        self.hover = False

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor("#4FB3B5" if is_dark() else "#0F7C80")
        line = accent if (self.hover or self.hasFocus()) else self.palette().color(QPalette.Mid)
        side = min(self.height() - 40, 190)
        x, y = 24, (self.height() - side) / 2
        r = side * 0.46
        cx, cy = x + side * (1.08 if self.hover else 0.96), y + side / 2   # the disc slides out on hover
        p.setPen(QPen(line, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))                # the disc, behind
        p.drawEllipse(QRectF(cx - r * 0.16, cy - r * 0.16, r * 0.32, r * 0.32))
        p.setBrush(self.palette().color(QPalette.Window))
        p.setPen(QPen(line, 2, Qt.DashLine if not self.hover else Qt.SolidLine))
        p.drawRoundedRect(QRectF(x, y, side, side), 4, 4)                 # the sleeve, in front
        p.setBrush(Qt.NoBrush)
        p.setPen(self.palette().color(QPalette.WindowText))
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        tx = x + side * 1.5 + 30                                            # after the disc
        p.drawText(QRectF(tx, y + side / 2 - 34, self.width() - tx - 16, 30), Qt.AlignLeft | Qt.AlignVCenter,
                   "Drop a folder of music here")
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.PlaceholderText))
        p.drawText(QRectF(tx, y + side / 2 - 2, self.width() - tx - 16, 44), Qt.AlignLeft | Qt.TextWordWrap,
                   "an album, a whole library, or loose files. Or click to choose a folder (Ctrl+O). "
                   "Nothing changes until you apply the preview.")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.hover = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self.hover = False
        self.update()

    def dropEvent(self, e):
        self.hover = False
        self.update()
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.dropped.emit(urls[0])

    def mousePressEvent(self, _e):
        self.choose()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.choose()
        else:
            super().keyPressEvent(e)

    def choose(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a music folder", str(Path.home() / "Music"))
        if d:
            self.dropped.emit(d)


def heading(text, hint=""):
    box = QVBoxLayout()
    t = QLabel(text)
    t.setProperty("role", "title")
    t.setAccessibleName(text)
    box.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setProperty("role", "hint")
        h.setWordWrap(True)
        box.addWidget(h)
    return box


def profile_names():
    return ["default"] + [n for n in pf.names()]


def load_profile(name):
    return pf.default() if name == "default" else pf.load(name)


def open_folder(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def search_web(album):
    """The system browser, searching for the album (no embedded browser)."""
    ident = album.get("identification") or {}
    rel = album.get("release") or {}
    words = " ".join(filter(None, [(rel.get("albumartist") or [""])[0], (rel.get("album") or [""])[0]])) \
        or Path(album["source_dir"]).name + " " + Path(album["source_dir"]).parent.name
    QDesktopServices.openUrl(QUrl("https://duckduckgo.com/?q=" + urllib.parse.quote(words + " album")))
    return ident


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
class Overview(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.plan = None
        self.path = None
        self.worker = None
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Overview", "Choose music, pick a profile, preview what will change, then apply."))
        self.drop = SleeveDrop()
        self.drop.setMinimumHeight(240)
        self.drop.dropped.connect(self.set_path)
        lay.addWidget(self.drop)
        row = QHBoxLayout()
        self.where = QLabel("No folder chosen")
        self.where.setProperty("role", "hint")
        row.addWidget(self.where, 1)
        row.addWidget(QLabel("Profile:"))
        self.profile = QComboBox()
        self.profile.setAccessibleName("Profile")
        row.addWidget(self.profile)
        self.ocr = QCheckBox("Read scans")
        self.ocr.setToolTip("Read barcodes and catalogue numbers from cover scans (slower)")
        row.addWidget(self.ocr)
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setShortcut(QKeySequence("Ctrl+P"))
        self.btn_preview.clicked.connect(self.preview)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setProperty("role", "primary")
        self.btn_apply.setShortcut(QKeySequence("Ctrl+Return"))
        self.btn_apply.clicked.connect(self.apply)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setShortcut(QKeySequence("Esc"))
        self.btn_cancel.clicked.connect(self.cancel)
        for b in (self.btn_preview, self.btn_apply, self.btn_cancel):
            row.addWidget(b)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setAccessibleName("Progress")
        self.bar.hide()
        lay.addWidget(self.bar)
        self.results = QWidget()
        rl = QVBoxLayout(self.results)
        rl.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the results (folder, status, reason)…")
        self.search.setAccessibleName("Search results")
        self.search.setClearButtonEnabled(True)
        rl.addWidget(self.search)
        split = QSplitter(Qt.Vertical)
        self.model = PlanModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setAccessibleName("Albums in the preview")
        self.table.selectionModel().selectionChanged.connect(self.show_detail)
        split.addWidget(self.table)
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        acts = QHBoxLayout()
        self.btn_anyway = QPushButton("Process anyway")
        self.btn_anyway.setToolTip("Rules will not exclude this folder; preview again to include it")
        self.btn_anyway.clicked.connect(self.process_anyway)
        self.btn_web = QPushButton("Search the web")
        self.btn_web.clicked.connect(lambda: self.selected() and search_web(self.selected()))
        self.btn_open = QPushButton("Open folder")
        self.btn_open.clicked.connect(lambda: self.selected() and open_folder(self.selected()["source_dir"]))
        self.btn_release = QPushButton("Use another release…")
        self.btn_release.setToolTip("Tag this album from the release you choose: paste its MusicBrainz or Discogs "
                                    "link (as Picard's \"Other versions\")")
        self.btn_release.clicked.connect(self.choose_release)
        self.forced = {}
        for b in (self.btn_anyway, self.btn_web, self.btn_open, self.btn_release):
            acts.addWidget(b)
        acts.addStretch(1)
        dl.addLayout(acts)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("What will change for the selected album")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        self.detail.setFont(mono)
        dl.addWidget(self.detail)
        split.addWidget(detail)
        split.setSizes([400, 220])
        rl.addWidget(split, 1)
        lay.addWidget(self.results, 1)
        self.results.hide()                                  # shown once there is a preview
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.spacer = QWidget()
        lay.addWidget(self.spacer, 1)                         # the empty state sits at the top
        self.refresh_profiles()
        self.busy(False)

    def refresh_profiles(self):
        cur = self.profile.currentText()
        self.profile.clear()
        self.profile.addItems(profile_names())
        if cur:
            self.profile.setCurrentText(cur)

    def busy(self, on):
        self.btn_preview.setEnabled(not on and bool(self.path))
        self.btn_apply.setEnabled(not on and bool(self.plan) and self.actionable() > 0)
        self.btn_cancel.setEnabled(on)
        self.bar.setVisible(on)
        for b in (self.btn_anyway, self.btn_web, self.btn_open, self.btn_release):
            b.setEnabled(not on and self.selected() is not None)
        if self.selected():
            self.btn_anyway.setEnabled(not on and self.selected()["status"] == "excluded")

    def actionable(self):
        return sum(1 for a in (self.plan or {}).get("albums", [])
                   if a["status"] in ("ready", "unresolved") and planmod.has_work(a))

    def set_path(self, path):
        self.path = path
        self.forced = {}
        self.where.setText(path)
        self.plan = None
        self.model.set_plan(None)
        self.detail.clear()
        self.busy(False)
        self.preview()

    def progress(self, i, n, what):
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i + 1)
        self.bar.setFormat(f"{i + 1} of {n}: {Path(what).name}")

    def preview(self):
        if not self.path:
            return
        try:
            prof = load_profile(self.profile.currentText())
        except pf.ProfileError as exc:
            QMessageBox.warning(self, APP, str(exc))
            return
        path, ocr, forced = self.path, self.ocr.isChecked(), dict(self.forced)

        def work(w):
            import engine
            e = engine.Engine(prof, ocr=ocr)
            e.force = forced
            return e.plan(path, progress=w.progress.emit, cancel=lambda: w.cancelled)
        self.busy(True)
        self.summary.setText("Identifying… nothing is changed while previewing.")
        self.worker = start(self, work, self.planned, self.progress, self.oops)

    def planned(self, plan):
        self.plan = plan
        self.results.show()
        self.spacer.hide()
        self.drop.setMinimumHeight(120)
        self.drop.setMaximumHeight(120)
        self.model.set_plan(plan)
        self.table.resizeColumnToContents(0)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        s = plan["summary"]
        notes = "; ".join(f"{k}: {v}" for k, v in (plan.get("provider_notes") or {}).items())
        self.summary.setText(", ".join(f"{v} {k}" for k, v in sorted(s.items()))
                             + (f"   ({notes})" if notes else ""))
        self.busy(False)
        if self.proxy.rowCount():
            self.table.selectRow(0)
        self.win.refresh_attention_badge(plan)

    def oops(self, msg):
        self.busy(False)
        self.summary.setText("Stopped: " + msg.splitlines()[0])
        QMessageBox.warning(self, APP, msg)

    def apply(self):
        n = self.actionable()
        if not n:
            return
        if not (self.plan.get("profile_data") or {}).get("unattended"):
            ok = QMessageBox.question(self, APP, f"Apply the previewed changes to {n} album(s)?\n\n"
                                      "Every change can be undone from Jobs.") == QMessageBox.Yes
            if not ok:
                return
        plan = self.plan

        def work(w):
            import executor
            return executor.Executor(plan, cancel=lambda: w.cancelled, progress=w.progress.emit).run()
        self.busy(True)
        self.summary.setText("Applying…")
        self.worker = start(self, work, self.applied, self.progress, self.oops)

    def applied(self, res):
        by_index = {r["index"]: r for r in res["albums"] if "index" in r}    # (two albums of one folder)
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
        self.summary.setText(f"Job {res['job']}: " + ", ".join(f"{v} {k}" for k, v in sorted(res["counts"].items()))
                             + ". Undo it from Jobs.")
        self.plan = None
        self.busy(False)
        self.win.pages["Jobs"].reload()

    def cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self.summary.setText("Cancelling after the current album…")

    def selected(self):
        idx = self.table.currentIndex() if hasattr(self, "table") else None
        if not idx or not idx.isValid():
            return None
        return self.proxy.data(self.proxy.index(idx.row(), 0), Qt.UserRole)

    def show_detail(self, *_):
        a = self.selected()
        if a is None:
            self.detail.clear()
        else:
            one = dict(self.plan or {"job": "", "profile": "", "operation": ""}, albums=[a], summary={a["status"]: 1})
            self.detail.setPlainText(planmod.preview(one, verbose=False))
        self.busy(False)

    def choose_release(self):
        a = self.selected()
        if not a:
            return
        import engine
        from PySide6.QtWidgets import QInputDialog
        key = os.path.realpath(a["source_dir"])
        files = sorted({fe["src"] for fe in a.get("files") or []} | {x.get("file") for x in a.get("skipped") or []
                                                                     if x.get("file")})
        was = self.forced.get(key)
        ref, ok = QInputDialog.getText(self, APP, f"The release for {Path(a['source_dir']).name}:\n"
                                       "a MusicBrainz or Discogs release link (or ID)",
                                       text=(was or {}).get("ref", "") if isinstance(was, dict) else (was or ""))
        if not ok:
            return
        ref = ref.strip()
        if not ref:
            self.forced.pop(key, None)
        elif engine.parse_release_ref(ref)[0] is None:
            QMessageBox.warning(self, APP, "That is not a MusicBrainz or Discogs release link.")
            return
        else:
            self.forced[key] = {"ref": ref, "files": files}    # (this album only: a folder may hold several)
        self.preview()

    def process_anyway(self):
        a = self.selected()
        if a and a["status"] == "excluded":
            folder = Path(a["source_dir"])
            mine = {fe["src"] for fe in a.get("files") or []} | {s.get("file") for s in a.get("skipped") or []} \
                | set(a.get("excluded_files") or [])
            mine.discard(None)
            try:                                       # other music in the folder or below it?
                others = any(p.suffix.lower() in ex.AUDIO_EXT and str(p) not in mine and p.is_file()
                             for p in folder.rglob("*"))
            except OSError:
                others = False
            if others and mine:                        # a loose track, or one album of a mixed folder:
                for f in sorted(mine):                 # only its own files, never the whole folder
                    ex.State().allow(f)
                what = Path(sorted(mine)[0]).name if len(mine) == 1 else f"{len(mine)} files in {folder.name}"
            else:
                ex.State().allow(folder)
                what = folder.name
            self.summary.setText(f"Rules will not exclude {what}. Preview again to include it.")


class Jobs(QWidget):
    def __init__(self, win):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Jobs", "Everything TagMender changed, newest first. Undo puts a job back as it was."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Started", "Job", "Status", "Profile", "Result"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for c in range(4):                                   # (a date and a job id are read whole)
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setAccessibleName("Jobs")
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo job")
        self.undo_btn.clicked.connect(self.undo)
        self.recover_btn = QPushButton("Recover interrupted jobs")
        self.recover_btn.clicked.connect(self.recover)
        self.albums_btn = QPushButton("Show albums")
        self.albums_btn.clicked.connect(self.show_albums)
        for b in (self.undo_btn, self.albums_btn, self.recover_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumHeight(160)
        lay.addWidget(self.out)
        self.reload()

    def reload(self):
        import executor
        rows = executor.jobs(200)
        self.table.setRowCount(len(rows))
        for i, (job, prof, started, _e, status, summary) in enumerate(rows):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else ""
            counts = ", ".join(f"{v} {k}" for k, v in json.loads(summary or "{}").items())
            for j, v in enumerate((when, job, status or "", prof or "", counts)):
                self.table.setItem(i, j, QTableWidgetItem(v))
        pending = executor.pending_jobs()
        self.recover_btn.setText(f"Recover interrupted jobs ({len(pending)})" if pending else "Recover interrupted jobs")
        self.recover_btn.setEnabled(bool(pending))

    def job(self):
        r = self.table.currentRow()
        return self.table.item(r, 1).text() if r >= 0 and self.table.item(r, 1) else None

    def undo(self):
        import executor
        job = self.job()
        if not job or QMessageBox.question(self, APP, f"Put back everything job {job} changed?") != QMessageBox.Yes:
            return
        try:
            self.out.setPlainText("\n".join(executor.undo(job)))
        except executor.StepError as exc:
            self.out.setPlainText(str(exc))
        self.reload()

    def recover(self):
        import executor
        lines = []
        busy = set(executor.busy_jobs())
        for j in executor.pending_jobs():
            if j in busy:
                lines.append(f"{j}: running in another TagMender process (the watcher?); not interrupted")
                continue
            try:
                lines += [f"{j}:"] + [f"  {x}" for x in executor.recover(j)]
            except executor.StepError as exc:
                lines.append(f"{j}: {exc}")
        self.out.setPlainText("\n".join(lines) or "Nothing to recover.")
        self.reload()

    def show_albums(self):
        import executor
        job = self.job()
        if job:
            rows = executor.job_albums(job)
            self.out.setPlainText("\n".join(f"{s:<10} {lb:<10} {a}" + (f"  ({d})" if d else "")
                                            for a, s, lb, d, _t in rows) or "No albums recorded.")


def watch_does(p) -> str:
    """What a profile does with an album dropped into one of its watched folders."""
    dest = p["destination"] or "?"
    return {"move": f"Moved into {dest}", "copy": f"Copied into {dest} (the drop stays)"}.get(
        p["operation"], "Tagged and renamed where it is (not moved)")


def _gui_prefs() -> dict:
    try:
        return json.loads((ex.CONFIG_DIR / "gui.json").read_text())
    except (OSError, ValueError):
        return {}


def _set_gui_pref(key, value):
    d = _gui_prefs()
    d[key] = value
    try:
        ex.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ex.CONFIG_DIR / ".gui.json.tmp"
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, ex.CONFIG_DIR / "gui.json")
    except OSError:
        pass


class WatchDialog(QDialog):
    """Add a watched folder, or change which profile one belongs to and how it is handled."""

    def __init__(self, parent, path="", profile=""):
        super().__init__(parent)
        self.setWindowTitle("Watched folder")
        self.resize(640, 0)
        self.old_path = path
        lay = QVBoxLayout(self)
        form = QFormLayout()
        row = QHBoxLayout()
        self.path = QLineEdit(path)
        self.path.setPlaceholderText("Folder music is dropped into, e.g. ~/Music/Inbox")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.prof = QComboBox()
        self.prof.addItems(pf.names())
        if profile:
            self.prof.setCurrentText(profile)
        self.does = QLabel("")
        self.does.setWordWrap(True)
        self.auto = QCheckBox("File albums without asking")
        self.auto.setToolTip("Off: each album is planned and waits under \"Waiting for your approval\".\n"
                             "This is a profile setting: it applies to every folder the profile watches.")
        self.settle = QDoubleSpinBox()
        self.settle.setRange(0, 1440)
        self.settle.setDecimals(1)
        self.settle.setSuffix(" min")
        self.settle.setToolTip("An album is left alone until nothing in it changed for this long\n"
                               "(a copy or download still running)")
        self.every = QDoubleSpinBox()
        self.every.setRange(0.1, 1440)
        self.every.setDecimals(1)
        self.every.setSuffix(" min")
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setProperty("role", "hint")
        form.addRow("Folder", row)
        form.addRow("Profile", self.prof)
        form.addRow("What happens", self.does)
        form.addRow("", self.auto)
        form.addRow("Wait until unchanged for", self.settle)
        form.addRow("Look every", self.every)
        lay.addLayout(form)
        lay.addWidget(self.warn)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.prof.currentTextChanged.connect(self.show_profile)
        self.path.textChanged.connect(lambda _t: self.show_warnings())
        self.show_profile(self.prof.currentText())

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder to watch", self.path.text() or str(Path.home()))
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
            errs.append("Choose the folder to watch.")
        if p is None:
            errs.append("Create a profile first (Profiles).")
            return errs, notes
        folder = Path(os.path.abspath(os.path.expanduser(text))) if text else None
        if folder is not None and not folder.is_dir():
            notes.append("This folder is not there now (not mounted?). It is watched once it is.")
        if p["operation"] == "in_place":
            notes.append("This profile fixes albums where they are, so dropped albums stay in this folder. "
                         "To file them into your library, choose a profile that moves or copies.")
        dest = Path(os.path.abspath(os.path.expanduser(p["destination"]))) if p["destination"] else None
        if folder is not None and dest is not None and p["operation"] != "in_place":
            if dest == folder:
                notes.append("This folder IS the profile's library: every album already in it will be "
                             "checked and filed again by this profile.")
            elif folder in dest.parents:
                notes.append("The library is inside this folder: what is in it is left alone, only new "
                             "drops are filed.")
            elif dest in folder.parents:
                notes.append("This folder is inside the profile's library.")
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
                        notes.append(f"Profile \"{n}\" watches this folder now; saving moves it to "
                                     f"\"{p.name}\".")
        if not self.auto.isChecked():
            notes.append("Albums are planned and wait for your approval on this page.")
        return errs, notes

    def show_warnings(self):
        errs, notes = self.problems()
        self.warn.setText("\n".join(errs + notes))

    def save(self):
        errs, _notes = self.problems()
        if errs:
            QMessageBox.information(self, APP, "\n".join(errs))
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
            QMessageBox.warning(self, APP, str(exc))
            return
        self.accept()


def _table(cols, stretch=0, fit=()):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    for c in fit:
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().hide()
    return t


def _fill(table, rows):
    table.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            table.setItem(i, j, QTableWidgetItem(str(v)))


class Watched(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.busy = False
        self.watcher = None
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Watched folders", "Music dropped into these folders - an album folder or loose "
                                                 "tracks - is identified, tagged and filed by the folder's "
                                                 "profile, once nothing in it has changed for a while."))
        self.table = _table(["Folder", "Profile", "What happens", "Applies", "Available"], stretch=2,
                            fit=(0, 1, 3, 4))
        self.table.doubleClicked.connect(lambda *_: self.change())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Watch a folder…")
        add.setProperty("role", "primary")
        add.clicked.connect(self.add)
        ch = QPushButton("Change…")
        ch.clicked.connect(self.change)
        rm = QPushButton("Stop watching")
        rm.clicked.connect(self.remove)
        self.check = QPushButton("Check now")
        self.check.clicked.connect(self.check_now)
        for b in (add, ch, rm, self.check):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.live = QCheckBox("Keep watching while TagMender is open")
        self.live.setChecked(bool(_gui_prefs().get("watch_in_app")))
        self.live.toggled.connect(self.set_live)
        row.addWidget(self.live)
        row.addStretch(1)
        self.svc_label = QLabel("")
        self.svc_btn = QPushButton("")
        self.svc_btn.clicked.connect(self.toggle_service)
        row.addWidget(self.svc_label)
        row.addWidget(self.svc_btn)
        lay.addLayout(row)
        box = QGroupBox("Waiting for your approval")
        bl = QVBoxLayout(box)
        self.pending = _table(["Album", "Filed into", "Profile"], stretch=1, fit=(0, 2))
        self.pending.doubleClicked.connect(lambda *_: self.show_plan())
        bl.addWidget(self.pending)
        prow = QHBoxLayout()
        for text, fn in (("Show plan", self.show_plan), ("Apply", self.apply_one), ("Apply all", self.apply_all),
                         ("Dismiss", self.dismiss)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            prow.addWidget(b)
        prow.addStretch(1)
        bl.addLayout(prow)
        lay.addWidget(box, 1)
        box = QGroupBox("Recently")
        bl = QVBoxLayout(box)
        self.recent = _table(["When", "Folder", "Outcome", "Profile"], stretch=1, fit=(0, 2, 3))
        bl.addWidget(self.recent)
        lay.addWidget(box, 1)
        self.out = QLabel("")
        self.out.setWordWrap(True)
        lay.addWidget(self.out)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_now)
        self.reload()
        self.set_live(self.live.isChecked(), save=False)

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
        rows = self.rows()
        _fill(self.table, [(path, p.name, watch_does(p), "Automatically" if p["unattended"] else "Asks first",
                            "✓ yes" if Path(path).expanduser().is_dir() else "✕ not mounted")
                           for path, p in rows])
        self.plans = []
        try:
            import watcher
            self.plans = watcher.pending_plans()
            prows = []
            for x in self.plans:
                for src, label, to in x["albums"] or [("(unreadable plan)", "", "")]:
                    if to and os.path.normpath(to) == os.path.normpath(src):
                        to = f"stays where it is ({label.lower() or 'tagged'})"
                    prows.append((src, to, x["profile"], x))
            self.prow_plans = [r[3] for r in prows]
            _fill(self.pending, [r[:3] for r in prows])
        except Exception:                                    # the page still shows the folders
            self.plans, self.prow_plans = [], []
            self.pending.setRowCount(0)
        try:
            import watcher
            _fill(self.recent, [(time.strftime("%Y-%m-%d %H:%M", time.localtime(ts or 0)), f, st, pn)
                                for f, pn, st, ts, _l in watcher.recent(40)])
        except Exception:
            self.recent.setRowCount(0)
        try:
            import watcher
            if watcher.service_supported():
                st = watcher.service_status()
                self.svc_label.setText(f"Background service: {st}")
                self.svc_btn.setText("Stop the background service" if st == "running" else "Run in the background")
                self.svc_btn.setToolTip("A systemd user service that keeps watching when TagMender is closed")
                self.svc_btn.show()
            else:
                self.svc_label.setText("")
                self.svc_btn.hide()
        except Exception:
            self.svc_label.setText("")
        self.win.set_badge("Watched folders", len(self.plans))
        if getattr(self, "timer", None) is not None and self.timer.isActive():
            ms = self.interval_ms()
            if ms != self.timer.interval():                  # "Look every" changed
                self.timer.start(ms)

    def selected(self):
        r = self.table.currentRow()
        if r < 0 or self.table.item(r, 0) is None:
            return None, None
        return self.table.item(r, 0).text(), self.table.item(r, 1).text()

    def add(self):
        if not pf.names():
            QMessageBox.information(self, APP, "Create a profile first (Profiles).")
            return
        if WatchDialog(self).exec():
            self.reload()

    def change(self):
        path, name = self.selected()
        if path and WatchDialog(self, path, name).exec():
            self.reload()

    def remove(self):
        path, name = self.selected()
        if not path:
            return
        try:
            data = pf.load(name).to_dict()
            data["sources"] = [s for s in data["sources"] if not (s.get("watch") and s.get("path") == path)]
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            QMessageBox.warning(self, APP, str(exc))
        self.reload()

    # ---- watching ------------------------------------------------------------
    def set_live(self, on, save=True):
        if save:
            _set_gui_pref("watch_in_app", bool(on))
        if not on:
            self.timer.stop()
            return
        self.timer.start(self.interval_ms())
        self.check_now()

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
                self.out.setText("No folder is watched yet: Watch a folder….")
            return
        self.busy = True

        def work(_w):
            import watcher
            w = watcher.Watcher(once=True)
            self.watcher = w
            res = w.tick()
            return res, getattr(w, "skipped", "")
        self.check.setEnabled(False)
        self.out.setText("Checking…")

        def done(out):
            res, skipped = out
            self.busy = False
            self.watcher = None
            self.check.setEnabled(True)
            self.out.setText(f"Skipped: {skipped}." if skipped else
                             "\n".join(f"{v}: {k}" for k, v in res.items()) or
                             f"Nothing new or changed ({time.strftime('%H:%M')}).")
            self.reload()
            if res:
                self.win.pages["Jobs"].reload()

        def failed(msg):
            self.busy = False
            self.watcher = None
            self.check.setEnabled(True)
            self.out.setText(f"The check failed: {msg}")
        start(self, work, done, failed=failed)

    def toggle_service(self):
        import watcher
        if watcher.service_status() == "running":
            msg = watcher.stop_service()
        else:
            if not self.rows():
                QMessageBox.information(self, APP, "Watch a folder first.")
                return
            msg = watcher.install_service()
        if msg not in ("running", "stopped"):
            QMessageBox.warning(self, APP, msg)
        self.reload()

    # ---- plans waiting for approval -----------------------------------------
    def chosen_plan(self):
        r = self.pending.currentRow()
        plans = getattr(self, "prow_plans", [])
        return plans[r] if 0 <= r < len(plans) else None

    def show_plan(self):
        x = self.chosen_plan()
        if not x:
            return
        try:
            text = planmod.preview(planmod.load(x["path"]))
        except Exception as exc:
            text = f"This plan cannot be read: {exc}"
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Plan {x['job']}")
        dlg.resize(900, 560)
        v = QVBoxLayout(dlg)
        t = QPlainTextEdit(text)
        t.setReadOnly(True)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    def _apply(self, plans):
        plans = [x for x in plans if not x.get("error")]
        if not plans:
            return

        def work(_w):
            import watcher
            out = []
            for x in plans:
                try:
                    res = watcher.apply_pending(x["path"])
                    out += [f"{r['status']}: {r['album']}" for r in res["albums"]]
                except Exception as exc:
                    out.append(f"not applied: {x['job']}: {exc}")
            return out

        def done(lines):
            self.out.setText("\n".join(lines))
            self.reload()
            self.win.pages["Jobs"].reload()
        self.out.setText("Applying…")
        start(self, work, done)

    def apply_one(self):
        x = self.chosen_plan()
        if x:
            self._apply([x])

    def apply_all(self):
        self._apply(list(self.plans))

    def dismiss(self):
        x = self.chosen_plan()
        if not x:
            return
        import watcher
        try:
            watcher.dismiss_pending(x["path"])
        except OSError as exc:
            QMessageBox.warning(self, APP, str(exc))
        self.reload()


class RuleDialog(QDialog):
    """Add or edit one Ignore/Skip rule: conditions joined by all (AND) or any (OR)."""

    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.setWindowTitle("Rule")
        self.resize(640, 360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(rule.name if rule else "")
        self.action = QComboBox()
        self.action.addItems(["ignore", "skip"])
        self.action.setToolTip("Ignore: left alone in every run while the rule applies. Skip: this run only.")
        self.target = QComboBox()
        self.target.addItems(["album", "file"])
        self.match = QComboBox()
        self.match.addItems(["all conditions (AND)", "any condition (OR)"])
        form.addRow("Name", self.name)
        form.addRow("Action", self.action)
        form.addRow("Applies to", self.target)
        form.addRow("Match", self.match)
        lay.addLayout(form)
        self.conds = QTableWidget(0, 4)
        self.conds.setHorizontalHeaderLabels(["Field", "Operator", "Text", "Case-sensitive"])
        self.conds.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.conds)
        row = QHBoxLayout()
        b = QPushButton("Add condition")
        b.clicked.connect(lambda: self.add_cond())
        r = QPushButton("Remove condition")
        r.clicked.connect(lambda: self.conds.removeRow(self.conds.currentRow()))
        row.addWidget(b)
        row.addWidget(r)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("Text is matched literally and ignoring case unless glob or regex is chosen: "
                      "[no vox] means exactly those characters.")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.rule_id = rule.id if rule else None
        self.enabled = rule.enabled if rule else True
        if rule:
            self.action.setCurrentText(rule.action)
            self.target.setCurrentText(rule.target)
            key = "any" if "any" in rule.when else "all"
            self.match.setCurrentIndex(1 if key == "any" else 0)
            for c in rule.when.get(key, []):
                if "field" in c:
                    self.add_cond(c)
        else:
            self.add_cond()

    def add_cond(self, c=None):
        c = c or {"field": "album", "op": "contains", "value": ""}
        r = self.conds.rowCount()
        self.conds.insertRow(r)
        f = QComboBox()
        f.setEditable(True)
        f.addItems(list(ex.FIELDS) + ["tag:"])
        f.setCurrentText(c["field"])
        o = QComboBox()
        o.addItems(list(ex.OPS))
        o.setCurrentText(c.get("op", "contains"))
        cs = QCheckBox()
        cs.setChecked(bool(c.get("case_sensitive")))
        self.conds.setCellWidget(r, 0, f)
        self.conds.setCellWidget(r, 1, o)
        self.conds.setItem(r, 2, QTableWidgetItem(str(c.get("value", ""))))
        self.conds.setCellWidget(r, 3, cs)

    def rule(self) -> ex.Rule:
        conds = []
        for r in range(self.conds.rowCount()):
            c = {"field": self.conds.cellWidget(r, 0).currentText().strip(),
                 "op": self.conds.cellWidget(r, 1).currentText(),
                 "value": (self.conds.item(r, 2).text() if self.conds.item(r, 2) else "")}
            if self.conds.cellWidget(r, 3).isChecked():
                c["case_sensitive"] = True
            if c["value"]:
                conds.append(c)
        name = self.name.text().strip() or "rule"
        return ex.Rule.from_dict({"id": self.rule_id or ex._slug(name), "name": name,
                                  "action": self.action.currentText(), "target": self.target.currentText(),
                                  "enabled": self.enabled,
                                  "when": {("any" if self.match.currentIndex() else "all"): conds}})


ART_SOURCE_CHOICES = (
    ("CoverUp, then Cover Art Archive, then Discogs", ["coverup", "caa", "discogs"]),
    ("CoverUp only (all its sources)", ["coverup"]),
    ("MusicBrainz: Cover Art Archive", ["caa"]),
    ("Discogs", ["discogs"]),
    ("MusicBrainz, then Discogs", ["caa", "discogs"]),
    ("Web: shops and web image search", ["web"]),
    ("The album's own scans only", ["local"]),
)


class Profiles(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addLayout(heading("Profiles"))
        self.list = QListWidget()
        self.list.setAccessibleName("Profiles")
        self.list.currentTextChanged.connect(self.show)
        left.addWidget(self.list, 1)
        nb = QPushButton("New profile…")
        nb.clicked.connect(self.new)
        left.addWidget(nb)
        lay.addLayout(left, 1)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 3)
        # settings tab
        st = QWidget()
        form = QFormLayout(st)
        self.desc = QLineEdit()
        self.op = QComboBox()
        self.op.addItems(list(pf.OPERATIONS))
        self.dest = QLineEdit()
        db = QPushButton("Choose…")
        db.clicked.connect(lambda: (lambda d: d and self.dest.setText(d))(
            QFileDialog.getExistingDirectory(self, "Destination")))
        drow = QHBoxLayout()
        drow.addWidget(self.dest, 1)
        drow.addWidget(db)
        self.primary = QComboBox()
        self.primary.addItems(list(pf.PRIMARY))
        self.mb = QCheckBox("MusicBrainz")
        self.dg = QCheckBox("Discogs")
        self.web = QCheckBox("Web (last resort)")
        prow = QHBoxLayout()
        for c in (self.mb, self.dg, self.web):
            prow.addWidget(c)
        self.t_id = QDoubleSpinBox()
        self.t_rep = QDoubleSpinBox()
        self.t_org = QDoubleSpinBox()
        for s in (self.t_id, self.t_rep, self.t_org):
            s.setRange(0, 100)
            s.setDecimals(0)
        self.naming = QComboBox()
        import naming
        self.naming.addItems(list(naming.PRESETS))
        self.formats = QLineEdit()
        self.formats.setPlaceholderText("every format (or e.g. mp3 opus m4a)")
        self.unres = QComboBox()
        self.unres.addItems(list(pf.UNRESOLVED_ACTIONS))
        self.art = QCheckBox("Find front covers")
        self.art_src = QComboBox()
        for label, srcs in ART_SOURCE_CHOICES:
            self.art_src.addItem(label, srcs)
        self.art_src.setToolTip("Where covers come from. CoverUp uses all the sources set in its options "
                                "(Picard > Options > Plugins > CoverUp); the others ask one place only.")
        self.art_when = QComboBox()
        self.art_when.addItem("Only for albums missing a cover", "needed")
        self.art_when.addItem("For every album", "always")
        self.art_enlarge = QCheckBox("Enlarge a small cover (down to 250 px) to the minimum size")
        self.art_artist = QCheckBox("Artist photo: artist.jpg in the artist folder and in each track "
                                    "(Enthusiast and Audiophile)")
        self.art_artist.setToolTip("From fanart.tv, TheAudioDB, Wikimedia Commons or Discogs (CoverUp's artist "
                                   "sources), once per artist.\nNever over an artist.jpg that is there; in the tracks "
                                   "as an 'Artist/performer' picture, beside the cover.")
        self.art_replace = QCheckBox("Replace an album's own cover when it is smaller than the minimum size")
        self.art_replace.setToolTip("By a larger cover found, or - when there is none - the album's own cover "
                                    "enlarged.\nThe old pictures and cover file are kept by the job: undo puts "
                                    "them back.")
        self.art_gen = QComboBox()
        for label, mode in (("Never", "off"), ("CoverUp's template (offline, free)", "template"),
                            ("CoverUp's AI service", "ai"), ("CoverUp's order: fan art, AI, template", "auto")):
            self.art_gen.addItem(label, mode)
        self.art_gen.setToolTip("When nothing is found. The AI service (local Stable Diffusion, Cloudflare, AI "
                                "Horde, ...) is chosen in CoverUp's options; a generated cover is marked as such "
                                "and replaced as soon as a real one is found.")
        # Picard's own tagging settings (Options > Metadata): as Picard is set, or this profile's
        self.pic_names = QComboBox()
        for label, v in (("As Picard is set", None), ("As credited on the release", "none"),
                         ("The artist's name for spelling variations (Picard 3's default)", "variations"),
                         ("The artist's name, also for former names", "all")):
            self.pic_names.addItem(label, v)
        self.pic_names.setToolTip("Picard's \"Standardize artist names\": a credit like \"Beatles\" is written as\n"
                                  "\"The Beatles\" unless it is one of the artist's former names (variations only)")
        self.pic_rel = QComboBox()
        self.pic_trk = QComboBox()
        self.pic_gen = QComboBox()
        for box in (self.pic_rel, self.pic_trk, self.pic_gen):
            for label, v in (("As Picard is set", None), ("On", True), ("Off", False)):
                box.addItem(label, v)
        self.pic_rel.setToolTip("Producer, engineer, mixer ... credited on the release (Picard's default: on)")
        self.pic_trk.setToolTip("Composer, lyricist, performers and their instruments, works (Picard's default: "
                                "off)")
        self.pic_gen.setToolTip("MusicBrainz genres, filtered as Picard's Options > Metadata > Genres says "
                                "(Picard's default: off; Discogs genres and styles come with Discogs Enrichment)")
        self.pic_v2 = QComboBox()
        for label, v in (("As Picard is set", None), ("As each file has it", "keep"), ("ID3v2.4", False),
                         ("ID3v2.3", True)):
            self.pic_v2.addItem(label, v)
        self.pic_v2.setToolTip("Picard's \"ID3v2 version to write\" for MP3, WAV and AIFF files (Picard 3's\n"
                               "default: v2.4; without Picard.ini each file keeps its own version). In v2.3\n"
                               "several values are joined with Picard's join character, \"/\" by default.")
        self.pic_v1 = QComboBox()
        for label, v in (("As Picard is set", None), ("Update one that is there", "keep"), ("Write", True),
                         ("Remove", False)):
            self.pic_v1.addItem(label, v)
        self.pic_v1.setToolTip("Picard's \"Write ID3v1 tags\" for MP3 files (Picard 3's default: write; without\n"
                               "Picard.ini an ID3v1 tag a file has is updated, none is added)")
        self.level = QComboBox()
        self.level.addItems(["basic", "enthusiast", "audiophile"])
        self.level.setToolTip("basic: artist, album, title, numbers, date, genre\n"
                              "enthusiast: everything MusicBrainz and Discogs supply, as Picard writes it\n"
                              "audiophile: also ReplayGain, lyrics, AcoustID, rip log, dynamic range")
        self.aud = {}
        arow = QHBoxLayout()
        for key, label in (("replaygain", "ReplayGain"), ("lyrics", "Lyrics"), ("acoustid", "AcoustID"),
                           ("rip_info", "Rip log"), ("quality", "Dynamic range / quality")):
            self.aud[key] = QCheckBox(label)
            arow.addWidget(self.aud[key])
        self.level.currentTextChanged.connect(lambda t: [c.setEnabled(t == "audiophile") for c in self.aud.values()])
        self.unattended = QCheckBox("Apply without asking (watched folders)")
        edition_labels = {"off": "Off", "edition": "Edition  ([Vinyl], [WEB], [Tape])",
                          "edition_year": "Edition + reissue year  ([2019 Vinyl], [2014 CD])"}
        self.editions = QComboBox()
        self.album_editions = QComboBox()
        for box in (self.editions, self.album_editions):
            for k in naming.EDITIONS:
                box.addItem(edition_labels[k], k)
        self.editions.setToolTip("Separate editions of one album in folder names: a vinyl rip, a CD and a\n"
                                 "download each get a folder of their own. An original CD gets no suffix;\n"
                                 "albums without a media tag keep their folder.")
        self.album_editions.setToolTip("Add the edition to the Album tag too: \"I\" becomes \"I [2019 Vinyl]\"")
        self.picard_btn = QPushButton("Picard script…")
        self.picard_btn.setToolTip("The Picard naming-script and tagger-script lines that do the same")
        self.picard_btn.clicked.connect(self.show_picard_script)
        erow = QHBoxLayout()
        erow.addWidget(self.editions, 1)
        erow.addWidget(self.picard_btn)
        for box in (self.editions, self.album_editions):
            box.currentIndexChanged.connect(lambda _i: self.picard_btn.setEnabled(
                self.editions.currentData() != "off" or self.album_editions.currentData() != "off"))
        form.addRow("Description", self.desc)
        form.addRow("Files", self.op)
        form.addRow("Destination", drow)
        form.addRow("Identify with", prow)
        form.addRow("Pressing decided by", self.primary)
        form.addRow("Identify at score ≥", self.t_id)
        form.addRow("Replace existing tags at ≥", self.t_rep)
        form.addRow("Rename or move at ≥", self.t_org)
        form.addRow("Naming", self.naming)
        form.addRow("Separate editions in folder names", erow)
        form.addRow("Edition in the Album tag", self.album_editions)
        form.addRow("Only formats", self.formats)
        form.addRow("Unresolved albums", self.unres)
        form.addRow("Tagging level", self.level)
        form.addRow("Artist names", self.pic_names)
        form.addRow("Release relationships", self.pic_rel)
        form.addRow("Track relationships", self.pic_trk)
        form.addRow("Genres from MusicBrainz", self.pic_gen)
        form.addRow("ID3v2 version (MP3)", self.pic_v2)
        form.addRow("ID3v1 tag (MP3)", self.pic_v1)
        form.addRow("Audiophile", arow)
        form.addRow("", self.art)
        form.addRow("Cover source", self.art_src)
        form.addRow("Look for covers", self.art_when)
        form.addRow("", self.art_enlarge)
        form.addRow("", self.art_replace)
        form.addRow("", self.art_artist)
        form.addRow("Create a cover when none is found", self.art_gen)
        form.addRow("", self.unattended)
        save = QPushButton("Save profile")
        save.setProperty("role", "primary")
        save.clicked.connect(self.save)
        form.addRow("", save)
        self.tabs.addTab(st, "Settings")
        # rules tab
        rt = QWidget()
        rl = QVBoxLayout(rt)
        hint = QLabel("Rules here add to the global rules (shown greyed). Ignored items are left alone in every "
                      "run while the rule applies; skipped items only in this run.")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        self.rules = QTableWidget(0, 5)
        self.rules.setHorizontalHeaderLabels(["On", "Name", "Action", "Applies to", "When"])
        self.rules.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rules.verticalHeader().hide()
        self.rules.itemChanged.connect(self.toggle_rule)
        self.rules.doubleClicked.connect(lambda *_: self.edit_rule())
        rl.addWidget(self.rules, 1)
        row = QHBoxLayout()
        for text, fn in (("Add rule…", self.add_rule), ("Edit…", self.edit_rule), ("Remove", self.remove_rule),
                         ("Test rules on a folder…", self.test_rules)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        self.test_out = QPlainTextEdit()
        self.test_out.setReadOnly(True)
        self.test_out.setAccessibleName("Rule test results")
        rl.addWidget(self.test_out, 1)
        self.tabs.addTab(rt, "Ignore and skip rules")
        self.reload()

    def scope(self):
        n = self.list.currentItem().text() if self.list.currentItem() else "global"
        return "global" if n == "(global rules)" else n

    def reload(self):
        cur = self.list.currentItem().text() if self.list.currentItem() else None
        self.list.clear()
        self.list.addItem("(global rules)")
        self.list.addItems(pf.names())
        if cur:
            m = self.list.findItems(cur, Qt.MatchExactly)
            if m:
                self.list.setCurrentItem(m[0])
        if not self.list.currentItem():
            self.list.setCurrentRow(0)

    def show(self, name):
        if not name:
            return
        settings_on = name != "(global rules)"
        self.tabs.setTabEnabled(0, settings_on)
        if settings_on:
            try:
                p = pf.load(name)
            except pf.ProfileError as exc:
                QMessageBox.warning(self, APP, str(exc))
                return
            self.desc.setText(p["description"])
            self.op.setCurrentText(p["operation"])
            self.dest.setText(p["destination"])
            self.primary.setCurrentText(p["primary"])
            self.mb.setChecked(p.provider_on("musicbrainz"))
            self.dg.setChecked(p.provider_on("discogs"))
            self.web.setChecked(p.provider_on("web", "enabled"))
            th = p["thresholds"]
            self.t_id.setValue(th["auto_min_confidence"])
            self.t_rep.setValue(th["replace_min_confidence"])
            self.t_org.setValue(th["organize_min_confidence"])
            self.naming.setCurrentText(p["naming"]["preset"])
            for box, key in ((self.editions, "editions"), (self.album_editions, "album_editions")):
                box.setCurrentIndex(max(0, box.findData(p["naming"].get(key, "off"))))
            self.picard_btn.setEnabled(self.editions.currentData() != "off" or
                                       self.album_editions.currentData() != "off")
            self.formats.setText(" ".join(p["formats"]))
            self.unres.setCurrentText(p["unresolved"]["action"])
            self.art.setChecked(p["artwork"]["enabled"])
            import artwork as _artwork
            srcs = _artwork.sources_of(p["artwork"])
            idx = self.art_src.findData(srcs)
            if idx < 0:                                 # an order set in the profile file: shown as it is
                self.art_src.addItem(" > ".join(srcs), srcs)
                idx = self.art_src.count() - 1
            self.art_src.setCurrentIndex(idx)
            self.art_when.setCurrentIndex(max(0, self.art_when.findData(p["artwork"].get("when", "needed"))))
            self.art_enlarge.setChecked(bool(p["artwork"].get("enlarge", True)))
            self.art_replace.setChecked(p["artwork"].get("replace", "larger_only") == "larger_only")
            self.art_artist.setChecked((p["artwork"].get("artist") or {}).get("enabled", True) is not False)
            self.art_gen.setCurrentIndex(max(0, self.art_gen.findData(p["artwork"].get("generate", "off"))))
            self.level.setCurrentText(p["tag_level"])
            pic = p.data.get("picard") or {} if hasattr(p, "data") else p.get("picard") or {}
            for box, key in ((self.pic_names, "standardize_artist_names"), (self.pic_rel, "release_ars"),
                             (self.pic_trk, "track_ars"), (self.pic_gen, "use_genres")):
                box.setCurrentIndex(max(0, box.findData(pic.get(key))))
            for box, key in ((self.pic_v2, "write_id3v23"), (self.pic_v1, "write_id3v1")):
                v = pic.get(key, "unset")
                box.setCurrentIndex(max(0, box.findData("keep" if v is None else (None if v == "unset" else v))))
            for k, c in self.aud.items():
                c.setChecked(bool(p["audiophile"].get(k, True)))
                c.setEnabled(p["tag_level"] == "audiophile")
            self.unattended.setChecked(p["unattended"])
        else:
            self.tabs.setCurrentIndex(1)
        self.load_rules()

    def load_rules(self):
        scope = self.scope()
        rules = ex.load_rules(None if scope == "global" else scope, include_disabled=True)
        self.rules.blockSignals(True)
        self.rules.setRowCount(len(rules))
        import tagmender
        for i, r in enumerate(rules):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if r.enabled else Qt.Unchecked)
            on.setData(Qt.UserRole, (r.id, r.scope))
            self.rules.setItem(i, 0, on)
            for j, v in enumerate((r.name, "Ignore" if r.action == "ignore" else "Skip",
                                   "album" if r.target == "album" else "file", tagmender.describe(r.when)), 1):
                it = QTableWidgetItem(v)
                if r.scope != scope:
                    it.setForeground(self.palette().color(QPalette.PlaceholderText))
                self.rules.setItem(i, j, it)
        self.rules.blockSignals(False)

    def toggle_rule(self, item):
        if item.column() != 0:
            return
        rid, scope = item.data(Qt.UserRole)
        ex.set_enabled(rid, item.checkState() == Qt.Checked, scope)

    def current_rule(self):
        r = self.rules.currentRow()
        if r < 0:
            return None
        rid, scope = self.rules.item(r, 0).data(Qt.UserRole)
        return next((x for x in ex.load_rules(None if scope == "global" else scope, include_disabled=True)
                     if x.id == rid and x.scope == scope), None)

    def add_rule(self, rule=None):
        dlg = RuleDialog(self, rule)
        while dlg.exec():
            try:
                r = dlg.rule()
                ex.save_rule(r, rule.scope if rule else self.scope())
                break
            except ex.RuleError as exc:
                QMessageBox.warning(self, APP, str(exc))
        self.load_rules()

    def edit_rule(self):
        r = self.current_rule()
        if r:
            self.add_rule(r)

    def remove_rule(self):
        r = self.current_rule()
        if r and QMessageBox.question(self, APP, f"Remove rule “{r.name}”?") == QMessageBox.Yes:
            ex.remove_rule(r.id, r.scope)
            self.load_rules()

    def test_rules(self):
        d = QFileDialog.getExistingDirectory(self, "Folder to test the rules on")
        if not d:
            return
        scope = self.scope()

        def work(_w):
            import tagmender
            rules = ex.load_rules(None if scope == "global" else scope)
            ov = ex.State().overrides()
            lines, n_files, n_ex = [], 0, 0
            for folder, files in tagmender.album_dirs(d):
                dec = ex.evaluate(files, rules, overrides=ov)
                n_files += len(files)
                n_ex += len(dec.excluded)
                whole = dec.summary()
                if whole:
                    lines.append(f"⊘ {whole.label:<8} {folder}   (rule “{whole.rule.name}”, matched {whole.where})")
                elif dec.excluded:
                    lines.append(f"◐ partly   {folder}")
                    lines += [f"     ⊘ {e.label:<8} {p.name}  (rule “{e.rule.name}”)" for p, e in dec.excluded.items()]
            return "\n".join(lines + [f"\n{n_ex} of {n_files} files would be excluded. Nothing was changed."])
        self.test_out.setPlainText("Testing…")
        start(self, work, self.test_out.setPlainText)

    def new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, APP, "Name of the new profile:")
        if ok and name.strip():
            try:
                pf.save(pf.from_dict({"name": name.strip()}))
            except pf.ProfileError as exc:
                QMessageBox.warning(self, APP, str(exc))
            self.reload()
            self.win.pages["Overview"].refresh_profiles()

    def picard_script_text(self) -> str:
        import naming
        try:
            tpl = naming.PRESETS.get(self.naming.currentText(), "")
        except AttributeError:
            tpl = ""
        return naming.picard_script(self.editions.currentData(), self.album_editions.currentData(), tpl)

    def show_picard_script(self):
        text = self.picard_script_text()
        if not text:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Picard script for editions")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("For Picard to name and tag as this profile does. (Picard works the edition out "
                             "per track: a set mixing media, such as a CD with a 7\" bonus disc, gets two "
                             "folders there; TagMender keeps it in one.)"))
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        lay.addWidget(box)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        copy = btns.addButton("Copy", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        dlg.resize(760, 360)
        dlg.exec()

    def save(self):
        name = self.scope()
        if name == "global":
            return
        data = pf.raw(name)
        data.update({"description": self.desc.text(), "operation": self.op.currentText(),
                     "destination": self.dest.text().strip(), "primary": self.primary.currentText(),
                     "formats": self.formats.text().split(), "unattended": self.unattended.isChecked()})
        prov = data.setdefault("providers", {})
        prov.setdefault("musicbrainz", {})["enabled"] = self.mb.isChecked()
        prov.setdefault("discogs", {})["enabled"] = self.dg.isChecked()
        prov.setdefault("web", {})["enabled"] = self.web.isChecked()
        data.setdefault("thresholds", {}).update({"auto_min_confidence": self.t_id.value(),
                                                  "replace_min_confidence": self.t_rep.value(),
                                                  "organize_min_confidence": self.t_org.value()})
        data.setdefault("naming", {})["preset"] = self.naming.currentText()
        data["naming"]["editions"] = self.editions.currentData()
        data["naming"]["album_editions"] = self.album_editions.currentData()
        data.setdefault("unresolved", {})["action"] = self.unres.currentText()
        data.setdefault("artwork", {})["enabled"] = self.art.isChecked()
        data["artwork"].update({"sources": list(self.art_src.currentData()), "when": self.art_when.currentData(),
                                "enlarge": self.art_enlarge.isChecked(), "generate": self.art_gen.currentData(),
                                "replace": "larger_only" if self.art_replace.isChecked() else "never"})
        data["artwork"]["artist"] = dict(data["artwork"].get("artist") or {}, enabled=self.art_artist.isChecked())
        data["artwork"].pop("provider", None)           # (sources says it now)
        data["tag_level"] = self.level.currentText()
        pic = dict(data.get("picard") or {})
        for box, key in ((self.pic_names, "standardize_artist_names"), (self.pic_rel, "release_ars"),
                         (self.pic_trk, "track_ars"), (self.pic_gen, "use_genres")):
            if box.currentData() is None:
                pic.pop(key, None)
            else:
                pic[key] = box.currentData()
        for box, key in ((self.pic_v2, "write_id3v23"), (self.pic_v1, "write_id3v1")):
            v = box.currentData()
            if v is None:
                pic.pop(key, None)
            else:
                pic[key] = None if v == "keep" else v
        data["picard"] = pic
        data.setdefault("audiophile", {}).update({k: c.isChecked() for k, c in self.aud.items()})
        try:
            pf.save(pf.from_dict(data))
            self.win.statusBar().showMessage(f"Saved profile {name}", 4000)
        except pf.ProfileError as exc:
            QMessageBox.warning(self, APP, str(exc))


class Attention(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Needs attention", "Albums marked Unresolved, with the reason. Retry looks them up "
                                                 "again; anything excluded by a rule is not listed here."))
        row = QHBoxLayout()
        self.root = QLineEdit()
        self.root.setPlaceholderText("Folder to look in")
        ch = QPushButton("Choose…")
        ch.clicked.connect(lambda: (lambda d: d and (self.root.setText(d), self.scan()))(
            QFileDialog.getExistingDirectory(self, "Folder")))
        sc = QPushButton("Look")
        sc.clicked.connect(self.scan)
        row.addWidget(self.root, 1)
        row.addWidget(ch)
        row.addWidget(sc)
        lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Reason", "Album folder", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        acts = QHBoxLayout()
        for text, fn in (("Retry in Overview", self.retry), ("Search the web", self.web), ("Open folder", self.open)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)

    def scan(self):
        path = self.root.text().strip()
        if not path:
            return

        def work(_w):
            import tagmender
            return tagmender._unresolved_albums(path)

        def done(rows):
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                code = ", ".join(r["codes"]) or "other"
                self.table.setItem(i, 0, QTableWidgetItem(f"! {code}"))
                self.table.item(i, 0).setToolTip("\n".join(reasons.describe(c) for c in r["codes"]))
                self.table.item(i, 0).setForeground(status_color("unresolved"))
                self.table.setItem(i, 1, QTableWidgetItem(r["folder"]))
                self.table.setItem(i, 2, QTableWidgetItem(r["reason"]))
            self.table.setSortingEnabled(True)
            self.win.set_badge("Needs attention", len(rows))
        start(self, work, done)

    def folder(self):
        r = self.table.currentRow()
        return self.table.item(r, 1).text() if r >= 0 else None

    def retry(self):
        f = self.folder()
        if f:
            self.win.go("Overview")
            self.win.pages["Overview"].set_path(f)

    def web(self):
        f = self.folder()
        if f:
            search_web({"source_dir": f})

    def open(self):
        f = self.folder()
        if f:
            open_folder(f)


class History(QWidget):
    def __init__(self, win):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addLayout(heading("History", "What the rules excluded, and why. Job results are under Jobs."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["When", "Excluded as", "Run", "Rule", "Item"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        b = QPushButton("Refresh")
        b.clicked.connect(self.reload)
        lay.addWidget(b, 0, Qt.AlignLeft)
        self.reload()

    def reload(self):
        rows = ex.State().history(500)
        self.table.setRowCount(len(rows))
        for i, (ts, job, item, label, rname, _d) in enumerate(rows):
            for j, v in enumerate((time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)), f"⊘ {label}", job,
                                   rname, item)):
                self.table.setItem(i, j, QTableWidgetItem(v))


class Settings(QWidget):
    def __init__(self, win):
        super().__init__()
        import credentials as cr
        self.cr = cr
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Settings", "Provider keys are stored only on this computer, readable by you alone, "
                                          "and are never shown again or written to logs."))
        box = QGroupBox("Provider keys")
        form = QFormLayout(box)
        self.fields = {}
        for name in ("discogs", "acoustid", "fanart", "audiodb", "lastfm"):
            e = QLineEdit()
            e.setEchoMode(QLineEdit.Password)
            e.setAccessibleName(cr.LABELS[name])
            src = cr.source(name)
            e.setPlaceholderText(f"set ({src})" if src else "not set")
            self.fields[name] = e
            form.addRow(cr.LABELS[name], e)
        sb = QPushButton("Save keys")
        sb.setProperty("role", "primary")
        sb.clicked.connect(self.save)
        form.addRow("", sb)
        lay.addWidget(box)
        chk = QGroupBox("Checks")
        cl = QVBoxLayout(chk)
        self.checks = QPlainTextEdit()
        self.checks.setReadOnly(True)
        cl.addWidget(self.checks)
        row = QHBoxLayout()
        rb = QPushButton("Run checks")
        rb.clicked.connect(self.run_checks)
        self.anon = QCheckBox("Replace folder and file names with codes")
        xb = QPushButton("Export diagnostics…")
        xb.clicked.connect(self.export)
        row.addWidget(rb)
        row.addStretch(1)
        row.addWidget(self.anon)
        row.addWidget(xb)
        cl.addLayout(row)
        lay.addWidget(chk, 1)

    def save(self):
        n = 0
        for name, e in self.fields.items():
            if e.text().strip():
                self.cr.store(name, e.text())
                e.clear()
                e.setPlaceholderText(f"set ({self.cr.source(name)})")
                n += 1
        QMessageBox.information(self, APP, f"Saved {n} key(s).")

    def run_checks(self):
        import doctor
        mark = {"ok": "✓", "warn": "!", "fail": "✕"}
        lines = []
        for c in doctor.checks():
            lines.append(f"{mark[c.status]} {c.name}: {c.detail}" + (f"\n    fix: {c.fix}" if c.fix else ""))
        self.checks.setPlainText("\n".join(lines))

    def export(self):
        import doctor
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostics",
                                              str(Path.home() / "tagmender-diagnostics.zip"), "Zip (*.zip)")
        if path:
            doctor.export(path, anonymize_paths=self.anon.isChecked())
            QMessageBox.information(self, APP, f"Saved {path}\nIt holds no keys or tokens.")


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------
PAGES = (("Overview", Overview), ("Jobs", Jobs), ("Watched folders", Watched), ("Profiles", Profiles),
         ("Needs attention", Attention), ("History", History), ("Settings", Settings))


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1180, 760)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        self.nav.setSpacing(1)
        self.nav.setAccessibleName("Sections")
        self.stack = QStackedWidget()
        self.pages = {}
        for i, (name, cls) in enumerate(PAGES):
            it = QListWidgetItem(name)
            it.setSizeHint(QSize(0, max(30, self.fontMetrics().height() + 14)))
            self.nav.addItem(it)
            page = cls(self)
            page.layout().setContentsMargins(22, 18, 22, 14)
            self.pages[name] = page
            self.stack.addWidget(page)
            act = QAction(self)
            act.setShortcut(QKeySequence(f"Alt+{i + 1}"))
            act.triggered.connect(lambda _=False, n=name: self.go(n))
            self.addAction(act)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        for key, fn in (("Ctrl+O", lambda: (self.go("Overview"), self.pages["Overview"].drop.choose())),
                        ("Ctrl+F", lambda: self.pages["Overview"].search.setFocus())):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
        import executor
        if set(executor.pending_jobs()) - set(executor.busy_jobs()):
            self.statusBar().showMessage("A job was interrupted. Recover it under Jobs.")

    def closeEvent(self, event):
        """Never end the program under a running job or watcher pass (the thread would be
        killed mid-album): ask them to stop, wait, then close."""
        self.pages["Watched folders"].finish()
        running = [th for page in self.pages.values() for th, _w in getattr(page, "_threads", [])
                   if _alive(th)]
        if not running:
            event.accept()
            return
        if getattr(self, "_closing", False) and time.time() - self._closing_since > 10:
            if QMessageBox.question(self, APP, "TagMender is still finishing an album (or waiting for the "
                                    "background watcher's pass). Close anyway? An interrupted job is put "
                                    "back as it was the next time TagMender starts.") == QMessageBox.Yes:
                event.accept()
                os._exit(0)
        event.ignore()
        self.statusBar().showMessage("Finishing the work in progress before closing…")
        if not getattr(self, "_closing", False):
            self._closing_since = time.time()
            self._closing = True
            from PySide6.QtCore import QTimer
            t = QTimer(self)
            t.timeout.connect(lambda: (not any(_alive(th) for page in self.pages.values()
                                               for th, _w in getattr(page, "_threads", []))) and self.close())
            t.start(200)

    def go(self, name):
        self.nav.setCurrentRow([n for n, _ in PAGES].index(name))

    def set_badge(self, name, n):
        item = self.nav.item([x for x, _ in PAGES].index(name))
        item.setText(f"{name}  ({n})" if n else name)

    def refresh_attention_badge(self, plan):
        n = sum(1 for a in plan["albums"] if a["status"] in ("unresolved", "blocked"))
        self.set_badge("Needs attention", n)


def main(argv=None):
    os.environ.setdefault("QT_ACCESSIBILITY", "1")
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP)
    app.setDesktopFileName("io.github.herculepirate.TagMender")
    app.setStyleSheet(stylesheet())
    win = Main()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
