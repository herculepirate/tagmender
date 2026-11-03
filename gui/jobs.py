"""gui/jobs.py - The Jobs page: what ran; undo a job, recover an interrupted one."""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import sheets
from .common import STATUS, counts_text, filter_table, heading, left_header, plural, popup_menu, status_cell
from .i18n import N_, tr
from .widgets import FlowLayout
from .work import start

# a job's status as the table shows it: a word, the colour of a style.STATUS key and a Lucide icon
# ("errors": a job that ended with failed albums; "interrupted": one still marked running that no
# process holds)
JOB_STATUS = {"done": (N_("Done"), "done", "circle-check"),
              "errors": (N_("Done with errors"), "unresolved", "circle-alert"),
              "partial": (N_("Done with errors"), "unresolved", "circle-alert"),
              "recovered": (N_("Recovered"), "unresolved", "rotate-ccw"),
              "undone": (N_("Undone"), "excluded", "undo-2"),
              "undone_problems": (N_("Partly undone"), "unresolved", "circle-alert"),
              "running": (N_("Running"), "ready", "loader-circle"),
              "interrupted": (N_("Interrupted"), "unresolved", "circle-alert"),
              "failed": (N_("Failed"), "failed", "circle-x")}
FAILED_KEYS = ("failed", "conflict", "cancelled")       # (albums a finished job did not do)
# why a job cannot be undone, for the button's tooltip and the menu's status tip
NOT_UNDOABLE = {"undone": N_("This job was already undone."), "running": N_("This job is still running."),
                "interrupted": N_("This job was interrupted: choose Tools > Recover Interrupted Jobs… first.")}


def shown_status(status, counts=None) -> str:
    """The JOB_STATUS key a job's stored status is shown as."""
    status = status or ""
    if status.startswith("undone with problems"):
        return "undone_problems"
    if status == "done" and any((counts or {}).get(k) for k in FAILED_KEYS):
        return "errors"
    return status


def undoable(status) -> bool:
    """Undo can run on a job that ended (or was recovered), and again on one undone with problems."""
    return shown_status(status) in ("done", "errors", "partial", "recovered", "undone_problems")


def status_item(status, counts=None) -> QTableWidgetItem:
    word, colour, icon = JOB_STATUS.get(shown_status(status, counts),
                                        ((status or "").capitalize(), "excluded", "circle-dot"))
    it = status_cell(QTableWidgetItem(), colour, tr(word), icon)
    it.setData(Qt.UserRole, status)                      # (the stored status: what Undo looks at)
    return it


def plain_error(msg) -> str:
    """executor.StepError's words (some written for the command line) as the app says them."""
    m = str(msg)
    if "was already undone" in m:
        return tr("This job was already undone.")
    if "did not finish" in m:
        return tr("This job was interrupted. Choose Tools > Recover Interrupted Jobs… first.")
    if "can no longer be undone" in m:
        return tr("This job can no longer be undone: its backups were removed") + \
            (f" on {m.rsplit(' on ', 1)[1]}" if " on " in m else "") + "."
    if "running in another TagMender process" in m:
        return tr("Another job is running (in another TagMender window, or the background watcher). Try again "
                  "once it ends.")
    if m.startswith("no job"):
        return tr("TagMender has no record of this job.")
    return m


def when_text(started, fmt="%Y-%m-%d %H:%M") -> str:
    return time.strftime(fmt, time.localtime(started)) if started else ""


class Jobs(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.working = False                                 # an undo or a recovery runs on a worker
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("Jobs"),
                              tr("Everything TagMender changed, newest first. Undo puts a job back as it was.")))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Started"), tr("Status"), tr("Profile"), tr("Result")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for c in range(3):                                   # (a date and a status are read whole)
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setTextElideMode(Qt.ElideRight)
        left_header(self.table)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setAccessibleName(tr("Jobs"))
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.activated.connect(lambda *_: self.open_details())   # (Return, or a double click)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setAccessibleName(tr("Job details"))
        self.out.setPlaceholderText(tr("Select a job and choose Show Albums."))
        self.out.hide()                                      # (shown once there is something to show)
        self.split = QSplitter(Qt.Vertical)
        self.split.setObjectName("pageSplit")
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.table)
        self.split.addWidget(self.out)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)
        lay.addWidget(self.split, 1)
        row = FlowLayout()
        self.undo_btn = QPushButton(tr("Undo Job…"))
        self.undo_btn.clicked.connect(self.undo)              # (clicked(False): undo() takes the selected job)
        self.recover_btn = QPushButton(tr("Recover Interrupted Jobs…"))
        self.recover_btn.clicked.connect(self.ask_recover)
        self.albums_btn = QPushButton(tr("Show Albums"))
        self.albums_btn.clicked.connect(self.show_albums)
        self.report_btn = QPushButton(tr("Show Report…"))
        self.report_btn.setToolTip(tr("What the job did, what needs your decision and what could not finish"))
        self.report_btn.clicked.connect(self.show_report)
        for b in (self.undo_btn, self.albums_btn, self.report_btn, self.recover_btn):
            b.setAccessibleName(b.text().rstrip("…"))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.pending = []
        self.rows = {}                                       # job -> (profile, started, status, counts)
        self.reload()

    def reload(self):
        import executor
        rows = executor.jobs(200)
        self.pending = executor.pending_jobs()
        try:
            busy = set(executor.busy_jobs())
        except Exception:
            busy = set()
        self.rows = {}
        self.table.setRowCount(len(rows))
        for i, (job, prof, started, _e, status, summary) in enumerate(rows):
            try:
                counts = json.loads(summary or "{}")
            except ValueError:
                counts = {}
            if status == "running" and job in self.pending and job not in busy:
                status = "interrupted"                       # (no process holds it: it was stopped)
            self.rows[job] = (prof or "", started, status, counts)
            first = QTableWidgetItem(when_text(started))
            first.setData(Qt.UserRole, job)                  # (the job's ID: a code, in the tooltip)
            first.setToolTip(tr("Job {job}").format(job=job))
            self.table.setItem(i, 0, first)
            self.table.setItem(i, 1, status_item(status, counts))
            self.table.setItem(i, 2, QTableWidgetItem(prof or ""))
            self.table.setItem(i, 3, QTableWidgetItem(counts_text(counts)))
        self.recover_btn.setText(tr("Recover Interrupted Jobs ({0})…").format(len(self.pending)) if self.pending
                                 else tr("Recover Interrupted Jobs…"))
        self.selection_changed()

    def status_of(self, job):
        return (self.rows.get(job) or ("", None, "", {}))[2]

    def undoable(self) -> bool:
        """The selected job can be undone (it ended, or was recovered; not one already undone)."""
        job = self.job()
        return bool(job) and undoable(self.status_of(job))

    def selection_changed(self):
        job = self.job()
        self.undo_btn.setEnabled(bool(job) and not self.working and self.undoable())
        why = tr(NOT_UNDOABLE.get(shown_status(self.status_of(job)), "")) if job and not self.undoable() else ""
        self.undo_btn.setToolTip(why or tr("Put back everything the selected job changed"))
        self.albums_btn.setEnabled(bool(job))
        self.report_btn.setEnabled(bool(job))
        self.recover_btn.setEnabled(bool(self.pending) and not self.working)
        if self.win is not None:
            a = self.win.act("undo_selected")
            a.setStatusTip(why or tr("Put back everything the job selected under Jobs changed"))
            self.win.update_album_actions()

    def context_menu(self, pos):
        popup_menu(self, self.table, pos, [self.win.act(k) if k else None for k in
                                                ("job_details", "show_job_albums", "show_report", None,
                                                 "undo_selected")]
                   if self.win is not None else [])

    def open_details(self):
        """Return or a double click on a job: its details (Job Details…), else its albums."""
        if self.win is not None and hasattr(self.win, "job_details") and self.job():
            self.win.job_details()
        else:
            self.show_albums()

    def filter(self, text):
        filter_table(self.table, text)

    def job(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 and self.table.selectionModel().hasSelection() else None
        return it.data(Qt.UserRole) if it else None

    def show_out(self, text):
        self.out.setPlainText(text)
        self.out.show()

    def overview_busy(self) -> bool:
        """An undo or recovery must not run beside an Apply (they could touch the same albums)."""
        ov = self.win.pages.get("Overview") if self.win is not None else None
        if (ov is not None and ov.running) or self.working:
            sheets.information(self, tr("Wait for the work in progress to finish."),
                               tr("A preview, an Apply or another undo is running. Undo or recover once it is done "
                                  "(or cancel it with File > Cancel)."))
            return True
        return False

    def say(self, text):
        """The outcome in the status bar too (the details pane may be out of sight)."""
        if self.win is not None:
            self.win.statusBar().showMessage(text, 8000)

    def run(self, what, fn, outcome=None):
        """fn() on a worker; its lines go to the details below the table, then the list is read
        again; outcome(lines) is the one line the status bar says."""
        self.working = True
        self.show_out(what)
        self.selection_changed()

        def done(lines):
            self.working = False
            self.show_out("\n".join(lines) or tr("Nothing to recover."))
            if outcome:
                self.say(outcome(lines))
            self.reload()

        def failed(msg):
            self.working = False
            first = plain_error(msg.splitlines()[0]) if msg else tr("That did not work.")
            self.show_out(first)
            self.say(first)
            self.reload()
        start(self, lambda _w: fn(), done, failed=failed)

    def job_words(self, job) -> str:
        """A job as the table names it: "the job from 31 Oct 16:17 (Library)" (its ID is a code)."""
        prof, started, _st, _c = self.rows.get(job) or ("", None, "", {})
        when = when_text(started, "%-d %b %H:%M")
        return (tr("the job from {when}")
                .format(when=when) if when else tr("this job")) + (f" ({prof})" if prof else "")

    def undo(self, job=None):
        import executor
        job = job or self.job()
        if not job or self.overview_busy():
            return
        try:
            n = len(executor.job_albums(job))
        except Exception:
            n = 0
        albums = plural(n, tr("album"), tr("albums")) if n else tr("its albums")
        words = self.job_words(job)
        name = words[:1].upper() + words[1:]
        head = f"Undo {words}" + (f", {plural(n, tr('album'), tr('albums'))}?" if n else "?")
        if not sheets.confirm(self, head,
                              tr("Puts back everything {words} changed: {albums}, their tags, file names, "
                                 "folders and covers, as they were before.").format(words=words, albums=albums),
                              tr("To make the changes again, preview the folder and apply it again. (Job {job})")
                              .format(job=job),
                              tr("Undo Job"), destructive=True):
            return

        def outcome(lines):
            problems = sum(1 for x in lines if x.startswith("PROBLEM"))
            return f"{name} undone" + (tr(", with {0} (see Jobs)").format(plural(problems, tr('problem'),
                                                                                 tr('problems'))) if problems
                                       else tr(": {albums} put back").format(albums=albums)) + "."
        # (a StepError - already undone, interrupted - reaches failed(), in the app's words)
        self.run(tr("Undoing {words}…").format(words=words), lambda: list(executor.undo(job)), outcome)

    def undo_last(self):
        """The newest job that can be undone (one already undone, running or interrupted is
        passed over: its sheet would end in an error)."""
        import executor
        rows = executor.jobs(200)
        job = next((r[0] for r in rows if undoable(self.status_of(r[0]) or r[4])), None)
        if job:
            self.undo(job)
        else:
            sheets.information(self, tr("There is no job to undo."),
                               tr("Every job listed under Jobs was undone already, or is still running."))

    def ask_recover(self):
        import executor
        if self.overview_busy():
            return
        n = len(set(executor.pending_jobs()) - set(executor.busy_jobs()))
        if not n:
            self.recover()
            return
        if sheets.confirm(self, tr("Recover {n} interrupted job{0}?").format('s' if n != 1 else '', n=n),
                          tr("Each album the job was working on when it stopped is put back as it was before "
                             "the job; albums it had finished stay done."),
                          tr("Finished albums can be undone afterwards from Jobs."), tr("Recover")):
            self.recover()

    @staticmethod
    def recover_lines(jobs):
        import executor
        lines = []
        for j in jobs:
            try:
                lines += [f"{j}:"] + [f"  {x}" for x in executor.recover(j)]
            except executor.StepError as exc:
                lines.append(f"{j}: {plain_error(exc)}")
        return lines

    def recover(self):
        """The jobs another process holds are named at once; the others are put back on a worker."""
        import executor
        busy = set(executor.busy_jobs())
        pending = executor.pending_jobs()
        lines = [tr("{j}: running in another TagMender process (the watcher?); not interrupted").format(j=j)
                 for j in pending if j in busy]
        todo = [j for j in pending if j not in busy]
        if not todo:
            self.show_out("\n".join(lines) or tr("Nothing to recover."))
            self.reload()
            return
        self.run(tr("Recovering the interrupted jobs…"), lambda: lines + self.recover_lines(todo),
                 lambda _lines: tr("Recovered {0} (details under Jobs).").format(plural(len(todo),
                                                                                        tr('interrupted job'),
                                                                                        tr('interrupted jobs'))))

    def show_report(self):
        """The selected job's report (gui.report): what it did, what is left for you."""
        if self.win is not None and self.job():
            from . import report
            return report.for_window(self.win)
        return None

    def show_albums(self):
        import executor
        job = self.job()
        if job:
            rows = executor.job_albums(job)
            word = lambda st: (tr(STATUS[st][1]) if st in STATUS  # noqa: E731
                               else (st or "").replace("_", " ").capitalize())
            words = self.job_words(job)
            self.show_out(f"{words[:1].upper() + words[1:]} (job {job})\n" + (
                "\n".join(f"{word(s):<22} {lb:<10} {a}" + (f"  ({d})" if d else "")
                          for a, s, lb, d, _t in rows) or tr("No albums recorded.")))
