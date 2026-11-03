"""gui/dialogs/logs.py - The log viewer, and a job's details with Undo.

LogViewer: tagmender.log (logsetup.log_dir(), or an older rotated file), shown by level (everything,
information, warnings, errors), searched as you type, following new lines as they are written;
Open Log Folder shows the folder. Files are read on a worker; a rotated file is read again whole.

JobDetails: what one job did, album by album and file by file - the tags it wrote (Tag: before ->
after), where it moved or copied each file, the covers and folders it made - read from the job's
journal and plan, and what an undo of it did. Undo Job… goes through executor.undo (the Jobs page's
own undo when the window hands it over).

The window calls show_logs(parent) and show_job_details(parent, job, undo=None)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .. import icons, sheets, style
from ..common import heading, open_folder, plural
from ..i18n import N_, tr
from ..tagnames import display
from ..work import start

# ---- the log --------------------------------------------------------------
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SHOW = ((N_("Everything"), "DEBUG"), (N_("Information and above"), "INFO"), (N_("Warnings and errors"), "WARNING"),
        (N_("Errors only"), "ERROR"))
LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:,\d+)? +(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
TAIL = 4_000_000                       # (a 100 MiB log: its last 4 MB are shown)
MAX_LINES = 50_000
FOLLOW_MS = 1000


def log_files() -> list:
    """The active log and the rotated ones beside it that exist, newest first."""
    import logsetup
    d = logsetup.log_dir()
    out = [d / "tagmender.log"]
    out += [d / f"tagmender.log.{i}" for i in range(1, 10) if (d / f"tagmender.log.{i}").exists()]
    return out


def read_log(path, start=0, tail=TAIL) -> dict:
    """{"text", "end", "ino", "cut", "missing"}: the file from `start` (its last `tail` bytes when
    read whole), "cut" when the start was left out; a rotated file (shorter now, or a new inode)
    is read whole again."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return {"text": "", "end": 0, "ino": None, "cut": False, "missing": True, "whole": True}
    whole = start <= 0 or st.st_size < start
    begin = max(0, st.st_size - tail) if whole else start
    with open(p, "rb") as fh:
        fh.seek(begin)
        data = fh.read(st.st_size - begin)
    if b"\n" in data:                                     # (a line still being written: the next read's)
        data = data[:data.rfind(b"\n") + 1]
    elif not whole:
        data = b""
    text = data.decode("utf-8", "replace")
    if whole and begin > 0:                               # (from the next whole line)
        text = text.partition("\n")[2]
    return {"text": text, "end": begin + len(data), "ino": st.st_ino, "cut": whole and begin > 0,
            "missing": False, "whole": whole}


def entries(text, last_level="INFO") -> list:
    """[(level, line)]: a line without a level of its own (a traceback) takes the one above it."""
    out = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            last_level = m.group(1)
        out.append((last_level, line))
    return out


def shown(items, level="DEBUG", search="") -> list:
    """The lines at `level` or above that hold `search` (any case)."""
    floor = LEVELS.index(level) if level in LEVELS else 0
    s = (search or "").casefold()
    return [line for lv, line in items if LEVELS.index(lv) >= floor and (not s or s in line.casefold())]


class _Levels(QSyntaxHighlighter):
    """Warnings and errors in their status colours (the word says it too)."""
    def highlightBlock(self, text):
        m = LINE.match(text)
        if not m or m.group(1) in ("DEBUG", "INFO"):
            return
        f = QTextCharFormat()
        f.setForeground(QColor(style.color("attention" if m.group(1) == "WARNING" else "error")))
        self.setFormat(m.start(1), len(m.group(1)), f)


class LogViewer(QDialog):
    """A window of its own (not modal: the app stays usable while it follows the log)."""

    def __init__(self, parent=None):
        super().__init__(sheets._window(parent))
        self.setWindowTitle(tr("TagMender Log"))
        self.setWindowFlag(Qt.Window, True)
        self.setMinimumSize(640, 400)
        self.resize(960, 600)
        self.items = []                                   # (level, line) of the file read
        self.end, self.ino = 0, None
        self.reading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        bar = QHBoxLayout()
        bar.setSpacing(style.SPACE["s"])
        self.file = QComboBox()
        self.file.setAccessibleName(tr("Log file"))
        flab = QLabel(tr("Log:"))
        flab.setBuddy(self.file)
        self.level = QComboBox()
        self.level.setAccessibleName(tr("Show"))
        for text, lv in SHOW:
            self.level.addItem(tr(text), lv)
        self.level.setCurrentIndex(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search the log"))
        self.search.setAccessibleName(tr("Search the log"))
        self.search.setClearButtonEnabled(True)
        self.follow = QCheckBox(tr("Follow new lines"))
        self.follow.setAccessibleName(tr("Follow new lines"))
        self.follow.setChecked(True)
        lab = QLabel(tr("Show:"))
        lab.setBuddy(self.level)
        bar.addWidget(flab)
        bar.addWidget(self.file)
        bar.addWidget(lab)
        bar.addWidget(self.level)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.follow)
        lay.addLayout(bar)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(style.mono())
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setAccessibleName(tr("Log"))
        self._hl = _Levels(self.text.document())
        lay.addWidget(self.text, 1)
        low = QHBoxLayout()
        self.caption = QLabel()
        self.caption.setProperty("role", "caption")
        low.addWidget(self.caption, 1)
        self.open_btn = QPushButton(tr("Open Log Folder"))
        self.open_btn.setAccessibleName(tr("Open Log Folder"))
        self.open_btn.clicked.connect(self.open_folder)
        self.reload_btn = QPushButton(tr("Reload"))
        self.reload_btn.setAccessibleName(tr("Reload"))
        self.reload_btn.setToolTip(tr("Read the log again (Ctrl+R)"))
        self.reload_btn.clicked.connect(self.reload)
        close = QPushButton(tr("Close"))                      # (a plain button: a log viewer's job is not to close)
        close.setAccessibleName(tr("Close"))
        close.setToolTip(tr("Close the log (Ctrl+W)"))
        close.clicked.connect(self.close)
        for b in (self.open_btn, self.reload_btn, close):
            b.setAutoDefault(False)
            low.addWidget(b)
        lay.addLayout(low)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.render)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.level.currentIndexChanged.connect(lambda _i: self.render())
        self.file.currentIndexChanged.connect(lambda _i: self.reload())
        for keys, fn in (("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll())),
                         ("Ctrl+W", self.close), ("Ctrl+R", self.reload)):     # (Esc closes it too: a dialog)
            QShortcut(QKeySequence(keys), self).activated.connect(fn)
        self.timer = QTimer(self)
        self.timer.setInterval(FOLLOW_MS)
        self.timer.timeout.connect(self.more)
        self.follow.toggled.connect(self.follow_changed)
        self.fill_files()
        self.reload()
        self.follow_changed(self.follow.isChecked())

    def fill_files(self):
        self.file.blockSignals(True)
        self.file.clear()
        for i, p in enumerate(log_files()):
            self.file.addItem(tr("Current log") if i == 0 else tr("Older log {i}").format(i=i), str(p))
        self.file.blockSignals(False)

    def path(self) -> Path:
        return Path(self.file.currentData() or log_files()[0])

    def follow_changed(self, on):
        live = on and self.file.currentIndex() <= 0           # (an older file is never written to)
        (self.timer.start if live else self.timer.stop)()

    def open_folder(self):
        d = self.path().parent
        if d.is_dir():
            open_folder(d)
        else:
            sheets.information(self, tr("There is no log folder yet."),
                               tr("TagMender makes it ({d}) the first time it writes to its log.").format(d=d))

    def reload(self):
        """Read the file whole (its last few MB) on a worker."""
        self.end, self.ino = 0, None
        self.read(0)
        self.follow_changed(self.follow.isChecked())

    def more(self):
        """New lines since the last read (the whole file again once it was rotated)."""
        if not self.reading:
            self.read(self.end)

    def read(self, offset):
        """On a worker; a result is used only if it is the latest read's, of the file shown now
        (a follow read still on its way when another file or Reload is picked is dropped)."""
        self.reading = True
        self._gen = gen = getattr(self, "_gen", 0) + 1
        path, ino = self.path(), self.ino

        def current():
            return gen == self._gen and path == self.path()

        start(self, lambda _w: self._read(path, offset, ino),
              lambda res: self.got(res) if current() else None,
              failed=lambda msg: self.read_failed(msg) if current() else None)

    @staticmethod
    def _read(path, offset, ino):
        try:
            now = os.stat(path).st_ino
        except OSError:
            now = None
        return read_log(path, 0 if (ino is not None and now != ino) else offset)

    def read_failed(self, msg):
        self.reading = False
        self._caption = ("", "")
        self.caption.setText(tr("The log cannot be read: ") + (msg.splitlines()[0] if msg else ""))

    def got(self, res):
        self.reading = False
        if res["missing"]:
            self.items, self.end, self.ino = [], 0, None
            self.text.setPlainText("")
            self.text.setPlaceholderText(tr("No log yet. TagMender writes one as soon as it previews, applies or "
                                            "watches a folder."))
            self.show_caption(tr("No log yet"))
            return
        self.ino = res["ino"]
        if res["whole"]:
            self.items = entries(res["text"])
            self.end = res["end"]
            self.cut = res["cut"]
            self.render()
            return
        self.end = res["end"]
        if not res["text"]:
            return
        new = entries(res["text"], self.items[-1][0] if self.items else "INFO")
        self.items += new
        if len(self.items) > MAX_LINES * 2:
            self.items = self.items[-MAX_LINES:]
        lines = shown(new, self.level.currentData(), self.search.text())
        if lines:
            bar = self.text.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 2
            self.text.appendPlainText("\n".join(lines))
            if at_end:
                bar.setValue(bar.maximum())
        self.say()

    def render(self):
        lines = shown(self.items, self.level.currentData(), self.search.text())
        if not lines and self.items:                      # (said in the text area, never a blank page)
            words = self.search.text().strip()
            self.text.setPlaceholderText(
                tr("No lines match “{words}” at this level.").format(words=words) if words else
                tr("No lines at this level: choose {0} under Show.").format(tr(SHOW[0][0])))
        self.text.setPlainText("\n".join(lines[-MAX_LINES:]))
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.say(len(lines))

    def say(self, n=None):
        if n is None:
            n = len(shown(self.items, self.level.currentData(), self.search.text()))
        words = f"{n:,} of {plural(len(self.items), tr('line'), tr('lines'))} shown"
        if getattr(self, "cut", False):
            words += tr(" (the end of a long file)")
        self.show_caption(words)

    def show_caption(self, words):
        """The footer: the count, then the log's path shortened in its middle to fit (whole in
        the tooltip; Open Log Folder shows it)."""
        self._caption = (words, str(self.path()))
        self.caption.setToolTip(self._caption[1])
        self.fit_caption()

    def fit_caption(self):
        words, path = getattr(self, "_caption", ("", ""))
        if not words:
            return
        fm = self.caption.fontMetrics()
        room = max(80, self.caption.width() - fm.horizontalAdvance(f"{words} · ") - 4)
        self.caption.setText(f"{words} · {fm.elidedText(path, Qt.ElideMiddle, room)}")
        self.caption.setAccessibleName(f"{words} · {path}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit_caption()

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


def show_logs(parent):
    """Open the log viewer, one per window (raised when it is already open)."""
    win = sheets._window(parent)
    v = getattr(win, "_log_viewer", None) if win is not None else None
    try:
        if v is not None:
            v.fill_files()
            v.reload()
            v.show()
            v.raise_()
            v.activateWindow()
            return v
    except RuntimeError:                                  # (deleted with its window)
        pass
    v = LogViewer(parent)
    if win is not None:
        win._log_viewer = v
    v.show()
    return v


# ---- a job's details --------------------------------------------------------
OPS = {"tag": N_("Tags written"), "move": N_("Moved"), "copy": N_("Copied"), "relink_move": N_("Moved"),
       "relink_copy": N_("Copied"),
       "none": N_("Left as it was")}


STATUS_WORDS = {"done": N_("Done"), "failed": N_("Failed"), "skipped": N_("Skipped"), "interrupted": N_("Interrupted")}
LINE_ROLE = Qt.UserRole + 1
STATUS_ROLE = Qt.UserRole + 2                         # an album row's status word (shown after its name)


def tail(path, parts=2) -> str:
    """A folder as a library names it: its last few parts ("Artist/Album (1998)"), the whole in a tooltip."""
    p = Path(str(path)).parts
    return "/".join(p[-parts:]) if len(p) > parts else str(path)


def _short(vals, n=80) -> str:
    s = "; ".join(str(v) for v in vals or []) or "(none)"
    return s if len(s) <= n else s[:n - 1] + "…"


def job_row(job):
    """The jobs table's row of this job (job, profile, started, ended, status, summary), or None:
    looked up by its ID, however old the job."""
    import executor
    db = executor._db()
    try:
        return db.execute("SELECT job, profile, started, ended, status, summary FROM jobs WHERE job=?",
                          (str(job),)).fetchone()
    finally:
        db.close()


def job_details(job) -> dict:
    """What a job did, from its journal and plan (read-only):
    {"job", "profile", "started", "status", "undoable", "why", "albums": [{"source", "status",
    "detail", "files": [{"src", "dst", "op", "changes": [(field, before, after)]}], "other": [lines]}],
    "undo": [lines]}."""
    import executor
    from ..jobs import NOT_UNDOABLE, shown_status, undoable
    jd = executor.job_dir(job)
    events = executor.Journal(jd).read() if jd.is_dir() else []
    try:
        planned = json.loads((jd / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        planned = {}
    row = job_row(job)
    status = row[4] if row else ""
    out = {"job": job, "profile": (row[1] if row else "") or planned.get("profile") or "",
           "started": row[2] if row else None, "status": status, "albums": [], "undo": []}
    begun = {}
    committed = set()
    for e in events:
        if e.get("ev") == "file" and e.get("state") == "begin":
            begun[(e["album"], e["idx"])] = e
        elif e.get("ev") == "file" and e.get("state") in ("committed", "source_removed"):
            committed.add((e["album"], e["idx"]))
    ended = {}
    other = {}
    for e in events:
        ev, a = e.get("ev"), e.get("album")
        if ev == "album_done":
            ended[a] = ("done", "")
        elif ev == "album_failed":
            ended[a] = ("failed", e.get("error") or "")
        elif ev == "album_rolled_back":
            prob = e.get("problems")
            ended[a] = ("failed", (ended.get(a, ("", ""))[1] + "; " if a in ended else "")
                        + (tr("rollback problems: ") + _short(prob, 200) if prob else tr("put back as it was")))
        elif ev in ("album_cancelled", "cancelled") and a is not None:
            ended[a] = ("skipped", e.get("label") or "cancelled")
        elif ev == "cover_file":
            other.setdefault(a, []).append(tr("Cover file saved: {0}").format(e.get('path')))
        elif ev == "new_file":
            other.setdefault(a, []).append(tr("File made: {0}").format(e.get('path')))
        elif ev == "dir_created":
            other.setdefault(a, []).append(tr("Folder made: {0}").format(e.get('path')))
        elif ev == "dir_removed":
            other.setdefault(a, []).append(tr("Empty folder removed: {0}").format(e.get('path')))
        elif ev == "companion" and e.get("state") == "begin":
            other.setdefault(a, []).append(f"{tr(OPS.get(e.get('op'), str(e.get('op')).capitalize()))}: "
                                           f"{Path(str(e.get('src'))).name} → {e.get('dst')}")
        elif ev == "undo_begin":
            out["undo"].append(tr("Undo started {0}").format(time.strftime('%Y-%m-%d %H:%M',
                                                                           time.localtime(e.get('t', 0)))))
        elif ev == "undone":
            out["undo"].append(tr("Undone: {0} put back").format(plural(e.get('restored') or 0, tr('file'),
                                                                        tr('files'))))
        elif ev == "undo_partial":
            out["undo"].append(tr("Partly undone: {0} put back").format(plural(e.get('restored') or 0, tr('file'),
                                                                               tr('files'))))
            out["undo"] += [f"  Problem: {p}" for p in e.get("problems") or []]
    for i, a in enumerate(planned.get("albums") or []):
        if i not in ended and not any(k[0] == i for k in begun):
            continue                                          # (an album the job did not touch)
        files = []
        for j, fe in enumerate(a.get("files") or []):
            if (i, j) not in committed:
                continue
            changes = [(r["field"], _short(r.get("before")), _short(r.get("after"))) for r in fe.get("diff") or []]
            files.append({"src": fe["src"], "dst": fe["dst"], "op": begun.get((i, j), {}).get("op") or fe["op"],
                          "changes": changes})
        st, detail = ended.get(i, ("interrupted", tr("the job stopped while working on it")))
        out["albums"].append({"source": a.get("source_dir") or "", "status": st, "detail": detail,
                              "files": files, "other": other.get(i, [])})
    why = ""
    if (jd / "PRUNED").exists():
        why = tr("Its backups were removed to save space, so it can no longer be undone.")
    elif not undoable(status):
        why = tr(NOT_UNDOABLE.get(shown_status(status), N_("This job cannot be undone.")))
    out["undoable"], out["why"] = not why, why
    return out


def _pages(widget) -> dict:
    """The pages of the window the widget is a sheet of (MainWindow.pages), or {}."""
    return getattr(sheets._window(widget.parent()), "pages", None) or {}


class JobDetails(QDialog):
    """A sheet: the job's albums, their files and each tag it wrote; Undo Job… and Close."""

    def __init__(self, parent, job, undo=None):
        super().__init__(sheets._window(parent))
        sheets.prepare(self, tr("Job Details"))
        self.job, self.undo_fn, self.data = job, undo, None
        self.setMinimumSize(600, 420)
        self.resize(860, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        box = heading(tr("Job Details"), tr("Reading the job…"))   # (as every sheet: the title, a hint below)
        self.head, self.hint = box.itemAt(0).widget(), box.itemAt(1).widget()
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addLayout(box)
        self.tree = QTreeWidget()
        self.tree.setObjectName("jobTree")
        self.tree.setColumnCount(3)
        # (a file: "01 Song.flac — moved", from where to where; a tag: its value before and after)
        self.tree.setHeaderLabels([tr("What"), tr("Before"), tr("After")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)        # (a path keeps its start and its file name)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        for c in (1, 2):                                  # (before and after share the room alike)
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
        self.tree.setAccessibleName(tr("What the job did"))
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.job_id = QLabel(tr("Job {job}")
                             .format(job=job))                  # (the job's ID, for a problem report: selectable)
        self.job_id.setProperty("role", "caption")
        self.job_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.job_id.setAccessibleName(tr("Job ID"))
        row.addWidget(self.job_id)
        row.addStretch(1)
        self.undo_btn = QPushButton(tr("Undo Job…"))
        self.undo_btn.setAccessibleName(tr("Undo Job"))
        self.undo_btn.setProperty("role", "destructive")
        self.undo_btn.setAutoDefault(False)
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        close = QPushButton(tr("Close"))
        close.setAccessibleName(tr("Close"))
        close.setDefault(True)
        close.setProperty("role", "primary")
        close.clicked.connect(self.accept)
        row.addWidget(self.undo_btn)
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        self.undo_btn.setEnabled(False)
        start(self, lambda _w: job_details(self.job), self.show_details, failed=self.show_error)

    def show_error(self, msg):
        self.head.setText(tr("This job cannot be read."))
        self.head.setAccessibleName(self.head.text())
        self.hint.setText(msg.splitlines()[0] if msg else "")

    def show_details(self, d):
        from ..jobs import JOB_STATUS, shown_status, when_text
        self.data = d
        word = tr(JOB_STATUS.get(shown_status(d["status"]), ((d["status"] or "").capitalize(),))[0])
        when = when_text(d["started"], "%-d %b %Y, %H:%M") if d.get("started") else ""
        # (what the sheet is, and when: the job's ID is a code, in the caption at the bottom)
        self.head.setText(tr("Job Details: {when}").format(when=when) if when else tr("Job Details"))
        self.head.setAccessibleName(tr("Job Details: the job of {when}")
                                    .format(when=when) if when else tr("Job Details"))
        self.hint.setText(" · ".join(x for x in (word, d["profile"],
                                                 plural(len(d["albums"]), tr("album"), tr("albums"))) if x)
                          + (f"\n{d['why']}" if d["why"] else ""))
        self.hint.setToolTip(tr("Job {0}").format(d['job']))
        self.job_id.setText(tr("Job {0}").format(d['job']))
        self.tree.clear()
        for a in d["albums"]:
            key = {"done": "done", "failed": "failed", "skipped": "skipped"}.get(a["status"], "unresolved")
            status = tr(STATUS_WORDS.get(a["status"], a["status"].capitalize()))
            # (the album's status in its own cell, after its name: the Before and After columns are paths)
            name = Path(a["source"]).name or a["source"]
            top = QTreeWidgetItem([f"{name} · {status}", "", ""])
            top.setToolTip(0, f"{a['source']}\n{status}")
            top.setIcon(0, icons.icon(style.status_icon(key), key, 14))      # (the word, its icon and colour)
            top.setData(0, Qt.AccessibleTextRole, f"{name}, {status}")
            top.setData(0, STATUS_ROLE, status)
            for f in a["files"]:
                moved = f["src"] != f["dst"]
                verb = tr(OPS.get(f["op"], f["op"])) if moved or not f["changes"] else tr("Tags written")
                item = QTreeWidgetItem([f"{Path(f['src']).name} — {verb.lower()}",
                                        tail(Path(f["src"]).parent) if moved else "",
                                        tail(Path(f["dst"]).parent) if moved else ""])
                item.setToolTip(0, f["src"])
                item.setToolTip(1, str(Path(f["src"]).parent))
                item.setToolTip(2, f["dst"])
                for field, before, after in f["changes"]:
                    c = QTreeWidgetItem([display(field), before, after])
                    c.setToolTip(0, field)
                    item.addChild(c)
                top.addChild(item)
            for line in a["other"]:
                top.addChild(self._line(line))
            if a["detail"]:                                    # (the whole reason, on a line of its own)
                why = self._line(a["detail"][:1].upper() + a["detail"][1:])
                why.setForeground(0, style.status_color(key))
                why.setIcon(0, icons.icon(style.status_icon(key), key, 14))
                top.addChild(why)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if d["undo"]:
            top = QTreeWidgetItem([tr("Undo"), "", ""])
            for line in d["undo"]:
                top.addChild(self._line(line))
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not d["albums"]:
            self.tree.addTopLevelItem(QTreeWidgetItem([tr("This job changed no album."), "", ""]))
        for item in self.tree.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            if item.data(0, LINE_ROLE):
                item.setFirstColumnSpanned(True)               # (a line of words across the columns)
        self.tree.resizeColumnToContents(0)
        self.tree.setColumnWidth(0, min(self.tree.columnWidth(0), max(240, self.tree.width() // 3)))
        self.undo_btn.setEnabled(d["undoable"])
        self.undo_btn.setToolTip(d["why"] or tr("Put back everything this job changed"))

    @staticmethod
    def _line(text):
        """A row of words (a cover saved, a folder made, why an album failed), across the columns."""
        it = QTreeWidgetItem([text, "", ""])
        it.setToolTip(0, text)
        it.setData(0, LINE_ROLE, True)
        it.setForeground(0, QColor(style.color("muted")))   # (a note under the album, not a row of it)
        return it

    def undo(self):
        """The Jobs page's undo (given, or the window's own Jobs page: it confirms, waits for an
        Apply, runs it on a worker and reloads the list); else the same here: never while the
        Overview works, a sheet that names what changes, executor.undo, then this job's details again."""
        pages = _pages(self)
        jobs_page = pages.get("Jobs")
        undo_fn = self.undo_fn or getattr(jobs_page, "undo", None)
        if undo_fn is not None:
            self.accept()
            undo_fn(self.job)
            return
        import executor
        ov = pages.get("Overview")
        if ov is not None and getattr(ov, "running", False):   # (as the Jobs page: never beside an Apply)
            sheets.information(self, tr("Wait for the work in progress to finish."),
                               tr("A preview or an Apply is running. Undo the job once it is done "
                                  "(or cancel it with File > Cancel)."))
            return
        n = len(self.data["albums"]) if self.data else 0
        if not sheets.confirm(self, tr("Undo this job, {0}?").format(plural(n, tr('album'),
                                                                            tr('albums'))) if n else tr("Undo this "
                                                                                                        "job?"),
                              tr("Puts back everything the job changed: its albums, their tags, file names, "
                                 "folders and covers, as they were before."),
                              tr("To make the changes again, preview the folder and apply it again. (Job {job})")
                              .format(job=self.job),
                              tr("Undo Job"), destructive=True):
            return
        self.undo_btn.setEnabled(False)
        self.head.setText(tr("Undoing the job…"))

        def finished():
            self.reload()

        def failed(msg):
            from ..jobs import plain_error
            sheets.warning(self, tr("The job was not undone."), plain_error(msg.splitlines()[0]) if msg else "")
            finished()
        start(self, lambda _w: list(executor.undo(self.job)), lambda _lines: finished(), failed=failed)


def show_job_details(parent, job, undo=None):
    """Open a job's details as a sheet of parent's window; undo(job): the Jobs page's undo, if given
    (offscreen: recorded, not shown)."""
    dlg = JobDetails(parent, job, undo)
    sheets.run(dlg, "job_details")
    return dlg
