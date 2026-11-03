"""gui/acoustid_submit.py - Album > Submit AcoustIDs…: send one album's fingerprints to AcoustID.

Never automatic: the user asks for it on one album, the files are fingerprinted on a worker
(read-only), and a sheet lists every track with the recording ID (and whether it is the preview's,
not yet written, or the file's own) and duration, the format and bitrate once, and word for word
what is sent where; only "Submit N Fingerprints" sends them. A second sheet then shows how each went, with Check Again
(AcoustID adds submissions a few minutes later). No file is changed.

Entry points (the integrator wires them; see the module's integration notes in the report):
  submit_album(win, album) -> Submission | None   the action: album is Overview.selected()
  enabled(album) -> bool                           whether the action makes sense for the album
  PREFS_KEY                                        the user key's (label, caption) for Preferences > Keys
Patch sites in tests: gui.acoustid_submit.engine.http_post / _fpcalc, gui.sheets.*."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QLabel, QPlainTextEdit, QTableWidgetItem, QToolButton, QWidget

import acoustid_submit as engine

from . import icons, sheets, style
from .common import _table, path_column, plural, status_cell
from .i18n import N_, tr
from .work import start

TITLE = N_("Submit AcoustIDs")
NEEDS = ("fpcalc", "acoustid", "acoustid_user")         # what a submission needs (Setup's rows)
PREFS_KEY = ("AcoustID user key", f"Your own key, for sending fingerprints: {engine.USER_KEY_URL} "
                                  "(not the application key)")
SETUP = N_("Open Setup")
# (the format and bitrate are said once, in the body; Sent says what stands for each track, or why not)
CONFIRM_COLS = ("#", N_("Title"), N_("Sent"), N_("Recording ID"), N_("From"), N_("Length"))
# where a recording ID was read (Track.id_from) -> a short word for the column, and its tooltip
ID_FROM = {"preview": N_("preview"), "tags": N_("tags")}
ID_FROM_TIP = {"preview": N_("From the preview: a match not yet written to the file, check it is right"),
               "tags": N_("From the file's own tags")}
ROOM_FOR_REQUEST = 640                   # (a sheet this tall shows the request open as well as the tracks)
RID_WIDTH = 150                          # (the recording ID's column: shortened in the middle, whole in its tooltip)
STATUS_COLS = ("#", N_("Title"), N_("Recording ID"), N_("Status"), N_("AcoustID"))
# an engine status -> a style.STATUS key and its icon
STATUS_KEYS = {"imported": "done", "pending": "ready"}


def enabled(album) -> bool:
    """An album with files (identified or tagged before): Submit AcoustIDs… can be chosen."""
    return bool(album) and bool(album.get("files") or album.get("album_files"))


def _length(secs) -> str:
    return "" if secs is None else f"{secs // 60}:{secs % 60:02d}"


def _parent(win):
    return win if isinstance(win, QWidget) else None


def _say(win, text, ms=6000):
    bar = getattr(win, "statusBar", None)
    if callable(bar):
        bar().showMessage(text, ms)


def submit_album(win, album):
    """Start Submit AcoustIDs… for one album; None when it cannot start (a sheet said why)."""
    parent = _parent(win)
    if not enabled(album):
        sheets.information(parent, tr("Choose an album first."), tr("Submit AcoustIDs sends the fingerprints of the "
                                                             "album selected in Overview."))
        return None
    need = engine.missing()
    if need:
        from . import setup
        need = [t for _i, t in setup.missing(NEEDS)] or need     # (in the app's words: "Add one in Setup")
        body = "\n".join(need) + (tr("\n\nThe application key identifies TagMender, the user key identifies you; "
                                     "both are yours and stay on this computer."))
        if sheets.ask(parent, tr("AcoustID is not set up for sending fingerprints."), body,
                      (tr("Cancel"), tr(SETUP)), tr(SETUP)) == tr(SETUP):
            first = next((i for i, _t in setup.missing(NEEDS)), None)
            setup.open_setup(win, first)                  # (Preferences, for a caller with no Setup page)
        return None
    s = Submission(win, album)
    s.fingerprint()
    return s


class Submission(QObject):
    """One album's submission, step by step: fingerprint -> confirm -> send -> status."""

    def __init__(self, win, album):
        super().__init__(win if isinstance(win, QObject) else None)
        self.win, self.album, self.tracks = win, album, []
        self.sheet = None                               # (the status sheet while it is open)
        self.worker = None
        self.error = ""
        self.cancelled = False

    # -- 1. fingerprint (worker) ------------------------------------------
    def fingerprint(self):
        album = self.album

        def work(w):
            tracks = engine.tracks_of(album)
            return engine.fingerprint(tracks, lambda i, n, name: w.progress.emit(i, n, name),
                                      lambda: w.cancelled)
        _say(self.win, tr("Reading the album's fingerprints…"), 0)
        self.worker = start(self, work, self.fingerprinted, self.progress, self.failed)
        self.busy(True)
        return self.worker

    def busy(self, on):
        """While the files are read: the toolbar's progress bar, and its Cancel (Ctrl+.) stops it."""
        win = self.win
        if not hasattr(win, "show_progress"):
            return
        if on:
            win.side_task = self
            win.progress.setRange(0, 0)                    # (how many is known once the first is read)
        elif getattr(win, "side_task", None) is self:
            win.side_task = None
        win.show_progress(on)
        if hasattr(win, "act"):
            ov = (getattr(win, "pages", None) or {}).get("Overview")
            win.act("cancel").setEnabled(on or bool(getattr(ov, "running", False)))

    def cancel(self):
        """Cancel: no more files are read, and nothing is sent."""
        if self.worker is not None:
            self.worker.cancelled = True
            self.cancelled = True
            _say(self.win, tr("Stopping: no more fingerprints are read, and nothing is sent."), 4000)

    def progress(self, i, n, name):
        _say(self.win, tr("Reading fingerprints: {0} of {n} ({name})…").format(i + 1, n=n, name=name), 0)
        bar = getattr(self.win, "progress", None)
        if bar is not None and hasattr(bar, "setRange"):
            bar.setRange(0, n)
            bar.setValue(i)

    def fingerprinted(self, tracks):
        self.busy(False)
        self.tracks = tracks
        _say(self.win, "", 1)
        if self.cancelled:                                 # (stopped: no sheet, nothing sent)
            _say(self.win, tr("Submit AcoustIDs stopped; nothing was sent."), 6000)
            return
        if not any(t.sendable for t in tracks):
            sheets.information(_parent(self.win), tr("There is nothing to send to AcoustID."),
                               tr("No track has both a fingerprint and a MusicBrainz recording ID. Identify the album "
                                  "first, or check that fpcalc reads the files.\n\n") + engine.describe(tracks))
            return
        self.confirm()

    # -- 2. confirm (sheet) -----------------------------------------------
    def action_text(self):
        n = sum(1 for t in self.tracks if t.sendable)
        return tr("Submit {0}").format(plural(n, tr("Fingerprint"), tr("Fingerprints")))

    def file_words(self) -> str:
        """The format and bitrate sent, said once: "FLAC, 912 kbit/s" (a range when the files differ)."""
        send = [t for t in self.tracks if t.sendable]
        fmts = sorted({t.fileformat for t in send if t.fileformat})
        rates = sorted({t.bitrate for t in send if t.bitrate})
        rate = "" if not rates else (f"{rates[0]} kbit/s" if len(rates) == 1 else f"{rates[0]}–{rates[-1]} kbit/s")
        return ", ".join(x for x in (" / ".join(fmts), rate) if x)

    def sent_text(self) -> str:
        """The "What is sent" box: the request (address, headers, fields), then each track's fields
        (engine.describe), word for word."""
        return engine.describe(self.tracks)

    @staticmethod
    def not_sent(trk) -> str:
        """Why a track is not sent, short enough for its row (the whole reason is its tooltip)."""
        if not trk.recording_id:
            return tr("Not sent: no recording ID")
        if not Path(trk.path).exists():
            return tr("Not sent: file missing")
        return tr("Not sent: no fingerprint")

    def confirm_sheet(self):
        """The sheet that says exactly what is sent; Cancel is safe, the action names the count."""
        n = sum(1 for t in self.tracks if t.sendable)
        k = len(engine.fallback(self.tracks))
        action = self.action_text()
        fmt = self.file_words() or tr("when known")
        tracks = plural(n, tr("track"), tr("tracks"))
        if not k:
            what = tr("Sent to api.acoustid.org for {0}: the fingerprint, length and MusicBrainz recording ID, and "
                      "the file's format and bitrate ({fmt}).").format(tracks, fmt=fmt)
        else:                                              # (the names go for some: said, not hidden)
            what = tr("Sent to api.acoustid.org for {0}: the fingerprint and length, and the file's format and "
                      "bitrate ({fmt}); with the MusicBrainz recording ID, except for {k}, whose title, artists, "
                      "album, track and disc numbers and year are sent in place of the recording ID.").format(
                tracks, fmt=fmt, k=plural(k, tr("track"), tr("tracks")))
        body = (what + " " + tr("With them go your AcoustID application key and user key (both under "
                                "Setup) and TagMender's version. No file names or paths are sent, and no file "
                                "changes.")
                + "\n\n" + tr("AcoustID adds the fingerprints to its public database under your account; this "
                               "cannot be undone from TagMender."))
        s = sheets.Sheet(_parent(self.win), tr("Send this album's fingerprints to AcoustID?"), body, "circle-alert",
                         "attention", (tr("Cancel"), action), action, title=tr(TITLE))
        s.setMaximumWidth(16777215)
        sent = self.sent_text()
        s.body_text = body + "\n\n" + sent
        t = _table(CONFIRM_COLS, stretch=1, fit=(0, 2, 4, 5))
        t.setAccessibleName(tr("Tracks to send"))
        t.setWordWrap(False)
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        t.setColumnWidth(3, RID_WIDTH)
        path_column(t, 3)                                  # (an ID shortened in the middle: both ends in view)
        t.setRowCount(len(self.tracks))
        for i, trk in enumerate(self.tracks):
            how = (tr("recording ID") if "mbid" in trk.args() else tr("title and names")) if trk.sendable else ""
            cells = (trk.tracknumber or "", trk.title or Path(trk.path).name, how, trk.recording_id,
                     tr(ID_FROM.get(trk.id_from, "")) if trk.recording_id else "", _length(trk.duration))
            for j, v in enumerate(cells):
                it = QTableWidgetItem(str(v))
                if not trk.sendable:
                    it.setForeground(style.status_color("skipped"))
                t.setItem(i, j, it)
            if not trk.sendable:                           # (why, in its row: not a greyed row alone)
                cell = status_cell(QTableWidgetItem(), "skipped", self.not_sent(trk))
                cell.setToolTip(trk.problem or tr("Not sent"))
                t.setItem(i, 2, cell)
            elif how == tr("title and names"):
                t.item(i, 2).setToolTip(tr("The fingerprint's length and MusicBrainz's track length differ by more "
                                           "than 30 seconds: the title, artists, album, track and disc numbers and "
                                           "year are sent in place of the recording ID."))
            elif trk.length_ms is None:
                t.item(i, 2).setToolTip(tr("MusicBrainz's track length is not known: the recording ID is sent "
                                           "without checking the lengths."))
            if trk.recording_id:
                t.item(i, 3).setToolTip(tr("MusicBrainz recording {v}").format(v=trk.recording_id) + "\n"
                                        + tr(ID_FROM_TIP.get(trk.id_from, "")))
            if trk.recording_id and trk.id_from in ID_FROM_TIP:
                t.item(i, 4).setToolTip(tr(ID_FROM_TIP[trk.id_from]))
        s.extra.addWidget(t, 1)                            # (the one part that grows)
        s.table = t
        box = QPlainTextEdit(sent)                         # (as the MusicBrainz sheets: the request itself)
        box.setReadOnly(True)
        box.setFont(style.mono())
        box.setAccessibleName(tr("What is sent"))
        box.setMinimumHeight(96)
        box.setMaximumHeight(200)                          # (the tracks keep the rest)
        show = QToolButton()                               # (a disclosure: open when the window has the room)
        show.setText(tr("Show exactly what is sent"))
        show.setAccessibleName(tr("Show exactly what is sent"))
        show.setCheckable(True)
        show.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        show.setStyleSheet("QToolButton { border: none; padding: 2px 0; }")
        show.toggled.connect(lambda on: (box.setVisible(on), show.setArrowType(Qt.DownArrow if on else Qt.RightArrow)))
        s.extra.addWidget(show, 0, Qt.AlignLeft)
        s.extra.addWidget(box)
        room = self.room()
        show.setChecked(room >= ROOM_FOR_REQUEST)
        show.toggled.emit(show.isChecked())
        s.resize(760, min(max(440, s.sizeHint().height()), room) if room else 520)
        if room:
            s.setMaximumHeight(room)
        s.show_sent, s.sent_box = show, box
        s.sent_text = sent
        return s

    def room(self) -> int:
        """The height a sheet may take over the window (0: no window to fit)."""
        w = _parent(self.win)
        w = w.window() if w is not None else None
        return max(320, w.height() - 64) if w is not None and w.height() > 0 else 0

    def confirm(self) -> bool:
        s = self.confirm_sheet()
        ok = sheets.run(s, "confirm") == QDialog.Accepted and s.clicked_text == self.action_text()
        s.deleteLater()
        if ok:
            self.send()
        return ok

    # -- 3. send (worker) -------------------------------------------------
    def send(self):
        tracks = self.tracks

        def work(_w):
            try:
                return engine.submit(tracks), ""
            except (engine.SubmitError, engine.credentials.CredentialError) as exc:
                return tracks, str(exc)
        _say(self.win, tr("Sending fingerprints to AcoustID…"), 0)
        self.worker = start(self, work, self.submitted, failed=self.failed)
        return self.worker

    def failed(self, msg):
        self.busy(False)
        _say(self.win, "", 1)
        self.error = msg
        sheets.warning(_parent(self.win), tr("Submit AcoustIDs did not work."), msg.split("\n\n")[0])

    def submitted(self, result):
        self.tracks, self.error = result
        _say(self.win, "", 1)
        if self.error:
            sheets.warning(_parent(self.win), tr("The fingerprints were not sent."), self.error)
            return
        _say(self.win, tr("AcoustID: {0}.").format(engine.summary(self.tracks)), 8000)
        self.show_status()

    # -- 4. status (sheet, Check Again on a worker) -----------------------
    def status_sheet(self):
        s = sheets.Sheet(_parent(self.win), self.status_headline(), self.status_body(), *self.status_icon(),
                         title=tr(TITLE))
        s.setMaximumWidth(16777215)
        s.resize(720, 400)
        t = _table(STATUS_COLS, stretch=1, fit=(0, 2, 3, 4))
        t.setAccessibleName(tr("Submission status"))
        s.extra.addWidget(t, 1)
        s.table = t
        note = QLabel(tr("Check Again sends the submission numbers, your AcoustID application key and TagMender's "
                         "version to api.acoustid.org; nothing else."))
        note.setProperty("role", "caption")
        note.setWordWrap(True)
        s.extra.addWidget(note)
        s.check = s.add_button(tr("Check Again"), on=self.check)
        s.check.setToolTip(tr("Ask AcoustID whether the fingerprints were added"))
        s.add_button(tr("Close"), default=True)
        self.sheet = s
        self.fill_status()
        return s

    def status_icon(self) -> tuple:
        """(icon, role): the check only when every track sent was added; else the attention mark."""
        sent = [t for t in self.tracks if t.submission_id is not None or t.sendable]
        done = bool(sent) and all(t.status == "imported" for t in sent)
        return ("circle-check", "identified") if done else ("circle-alert", "attention")

    def status_headline(self):
        return f"AcoustID: {engine.summary(self.tracks)}."

    def status_body(self):
        waiting = any(t.status == "pending" for t in self.tracks)
        return (tr("AcoustID adds new fingerprints a few minutes after they arrive: choose Check Again to see "
                   "whether they were added.") if waiting else tr("Thank you: the fingerprints help everyone identify "
                                                           "this music."))

    def fill_status(self):
        s = self.sheet
        if s is None:
            return
        t = s.table
        t.setRowCount(len(self.tracks))
        for i, trk in enumerate(self.tracks):
            for j, v in enumerate((trk.tracknumber or "", trk.title or Path(trk.path).name, trk.recording_id)):
                t.setItem(i, j, QTableWidgetItem(str(v)))
            word = trk.status_words() if (trk.submission_id is not None or trk.problem
                                          or trk.status) else tr("Not sent")
            key = STATUS_KEYS.get(trk.status, "failed" if trk.status.startswith("Not sent") else "skipped")
            t.setItem(i, 3, status_cell(QTableWidgetItem(), key, word))
            t.setItem(i, 4, QTableWidgetItem(trk.acoustid))
        s.headline_text, s.body_text = self.status_headline(), self.status_body()
        for i, text in enumerate((s.headline_text, s.body_text)):   # (the sheet's headline and body labels)
            s.col.itemAt(i).widget().setText(text)
        icon, role = self.status_icon()
        s.icon.setPixmap(icons.icon(icon, role, 32).pixmap(32, 32))
        s.icon.setAccessibleName(tr("Done") if icon == "circle-check" else tr("Attention"))
        s.check.setEnabled(any(trk.submission_id is not None and trk.status != "imported" for trk in self.tracks))

    def show_status(self):
        s = self.status_sheet()
        sheets.run(s, "status")
        if sheets.blocking():
            self.sheet = None
            s.deleteLater()
        return s

    def check(self):
        tracks = self.tracks

        def work(_w):
            try:
                return engine.check_status(tracks), ""
            except (engine.SubmitError, engine.credentials.CredentialError) as exc:
                return tracks, str(exc)
        if self.sheet is not None:
            self.sheet.check.setEnabled(False)
            self.sheet.check.setText(tr("Checking…"))
        self.worker = start(self, work, self.checked, failed=self.failed)
        return self.worker

    def checked(self, result):
        self.tracks, self.error = result
        if self.sheet is not None:
            self.sheet.check.setText(tr("Check Again"))
            self.fill_status()
        if self.error:
            sheets.warning(_parent(self.win), tr("AcoustID did not say how the submission went."), self.error)
