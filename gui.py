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
                            QThread, QUrl, Signal)
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


def start(owner, fn, done, progress=None, failed=None):
    """Run fn(worker) on a thread; done(result) on the UI thread."""
    th = QThread(owner)
    w = Worker(fn)
    w.moveToThread(th)
    th.started.connect(w.run)
    w.finished.connect(done)
    w.failed.connect(failed or (lambda msg: QMessageBox.warning(owner, APP, msg)))
    if progress:
        w.progress.connect(progress)
    for sig in (w.finished, w.failed):
        sig.connect(th.quit)
    th.finished.connect(th.deleteLater)
    owner._threads = getattr(owner, "_threads", []) + [(th, w)]
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
        for b in (self.btn_anyway, self.btn_web, self.btn_open):
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
        for b in (self.btn_anyway, self.btn_web, self.btn_open):
            b.setEnabled(not on and self.selected() is not None)
        if self.selected():
            self.btn_anyway.setEnabled(not on and self.selected()["status"] == "excluded")

    def actionable(self):
        return sum(1 for a in (self.plan or {}).get("albums", [])
                   if a["status"] in ("ready", "unresolved") and planmod.has_work(a))

    def set_path(self, path):
        self.path = path
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
        path, ocr = self.path, self.ocr.isChecked()

        def work(w):
            import engine
            e = engine.Engine(prof, ocr=ocr)
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


class Watched(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.addLayout(heading("Watched folders", "Folders kept processed in the background "
                                                 "(tagmender watch, or the user service in contrib/). "
                                                 "Each belongs to a profile."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Folder", "Profile", "Available"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.prof = QComboBox()
        add = QPushButton("Watch a folder…")
        add.clicked.connect(self.add)
        rm = QPushButton("Stop watching")
        rm.clicked.connect(self.remove)
        self.check = QPushButton("Check now")
        self.check.clicked.connect(self.check_now)
        row.addWidget(QLabel("For profile:"))
        row.addWidget(self.prof)
        for b in (add, rm, self.check):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.out = QLabel("")
        self.out.setWordWrap(True)
        lay.addWidget(self.out)
        self.reload()

    def reload(self):
        self.prof.clear()
        self.prof.addItems(pf.names())
        rows = []
        for n in pf.names():
            try:
                p = pf.load(n)
            except pf.ProfileError:
                continue
            rows += [(s["path"], n) for s in p["sources"] if s.get("watch")]
        self.table.setRowCount(len(rows))
        for i, (path, n) in enumerate(rows):
            ok = Path(path).expanduser().is_dir()
            for j, v in enumerate((path, n, "✓ yes" if ok else "✕ not mounted")):
                self.table.setItem(i, j, QTableWidgetItem(v))

    def add(self):
        if not self.prof.currentText():
            QMessageBox.information(self, APP, "Create a profile first (Profiles).")
            return
        d = QFileDialog.getExistingDirectory(self, "Folder to watch")
        if not d:
            return
        p = pf.load(self.prof.currentText())
        data = p.to_dict()
        data["sources"] = [s for s in data["sources"] if s.get("path") != d] + [{"path": d, "watch": True,
                                                                                "recursive": True}]
        try:
            pf.save(pf.from_dict(data))
        except pf.ProfileError as exc:
            QMessageBox.warning(self, APP, str(exc))
        self.reload()

    def remove(self):
        r = self.table.currentRow()
        if r < 0:
            return
        path, name = self.table.item(r, 0).text(), self.table.item(r, 1).text()
        data = pf.load(name).to_dict()
        data["sources"] = [s for s in data["sources"] if s.get("path") != path]
        pf.save(pf.from_dict(data))
        self.reload()

    def check_now(self):
        def work(_w):
            import watcher
            return watcher.Watcher(once=True).tick()
        self.check.setEnabled(False)
        self.out.setText("Checking…")

        def done(res):
            self.check.setEnabled(True)
            self.out.setText("\n".join(f"{v}: {k}" for k, v in res.items()) or "Nothing new or changed.")
            self.win.pages["Jobs"].reload()
        start(self, work, done)


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
        self.art = QCheckBox("Find front covers (CoverUp first)")
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
        form.addRow("Audiophile", arow)
        form.addRow("", self.art)
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
            self.level.setCurrentText(p["tag_level"])
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
        data["tag_level"] = self.level.currentText()
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
