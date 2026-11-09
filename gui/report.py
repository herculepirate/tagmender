"""gui/report.py - The run report, as a sheet: what a job (or the preview) did and what is left for you.

Three sections (report.py builds them): Needs your decision, Could not finish, Done (Ready to apply,
for a preview). Each album: its folder as a link that opens it in the file manager, the issue in
plain words, and the releases that fit best, each linking to its MusicBrainz or Discogs page with
an Apply This Release button. That button only previews the album with the release on the Overview
(Overview.preview_release): nothing is written until you press Apply there, which asks first.
Export saves the report as a web page or a CSV file.

    open_report(win, rep)            the sheet for a report (report.build / for_job / for_attention)
    for_window(win)                  the report of what the page shown has: the selected job, the
                                     preview, or the albums Needs attention found"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from . import sheets, style
from .common import heading
from .i18n import N_, tr
from .work import start

SECTION_WORDS = {"decide": N_("Needs your decision"), "failed": N_("Could not finish"), "done": N_("Done"),
                 "ready": N_("Ready to apply")}
COLUMNS = (N_("Album"), N_("Folder"), N_("Issue"), N_("Suggested release"), "")
# report.py's words (it has no Qt): its status words, notes and title, shown translated
STATUS_WORDS = {"Partial": N_("Partial"), "Album only": N_("Album only"), "Split": N_("Split"),
                "Mixed": N_("Mixed"), "Check": N_("Check"), "Stopped": N_("Stopped"), "Conflict": N_("Conflict"),
                "Failed": N_("Failed"), "Blocked": N_("Blocked"), "Unresolved": N_("Unresolved"),
                "Done": N_("Done"), "Ready": N_("Ready")}
NOTE_WORDS = (N_("This job was undone since: its albums were put back as they were before it."),
              N_("This job is still running (or was interrupted): what it has not reached yet is not listed."))
ATTENTION_WORDS = N_("Albums marked Unresolved or Partial")
COL_ALBUM, COL_FOLDER, COL_ISSUE, COL_RELEASE, COL_APPLY = range(5)


def open_link(url) -> bool:
    """A folder (file://) in the file manager, a release page in the browser."""
    return QDesktopServices.openUrl(QUrl(str(url)))


def short_path(path) -> str:
    """A folder by its last two parts ("Artist/Album"): the whole path is in the tooltip."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:]) or str(path)


def _link_html(text, url, colour="") -> str:
    style = f" style='color:{colour}'" if colour else ""
    return f"<a href='{html.escape(str(url), quote=True)}'{style}>{html.escape(str(text))}</a>"


def plain_tip(text) -> str:
    """A tooltip that shows text as it is: a tag that looks like markup ("<b>B</b>") is never
    rendered as rich text."""
    return "<qt>" + html.escape(str(text)).replace("\n", "<br>") + "</qt>"


def status_words(status) -> str:
    return tr(STATUS_WORDS[status]) if status in STATUS_WORDS else str(status)


def heading_words(rep) -> str:
    """report.heading, in the app's language."""
    import time

    import report
    if rep.get("title"):
        return tr(ATTENTION_WORDS) if rep["title"] == report.ATTENTION_TITLE else str(rep["title"])
    job, prof = rep.get("job") or "", rep.get("profile") or ""
    if not rep.get("applied"):
        return tr("Report of plan {job} (not applied yet; {profile})").format(job=job, profile=prof)
    if rep.get("started"):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rep["started"]))
        return tr("Report of job {job} ({when}, {profile})").format(job=job, when=when, profile=prof)
    if prof:
        return tr("Report of job {job} ({profile})").format(job=job, profile=prof)
    return tr("Report of job {job}").format(job=job)


def note_words(note) -> str:
    """report.py's note (undone, still running), in the app's language."""
    return tr(note) if note in NOTE_WORDS else str(note or "")


def _link(text, url, tip="") -> QLabel:
    lab = QLabel(_link_html(text, url))
    lab.setProperty("words", str(text))
    lab.setProperty("url", str(url))
    lab.setTextFormat(Qt.RichText)
    lab.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
    lab.setOpenExternalLinks(False)                    # (opened by open_link: one place, and testable)
    lab.linkActivated.connect(open_link)
    lab.setToolTip(plain_tip(tip or str(url)))
    lab.setAccessibleName(str(text))
    return lab


class ReportSheet(QDialog):
    def __init__(self, win, rep):
        super().__init__(sheets._window(win))
        sheets.prepare(self, tr("Report"))
        self.win, self.rep = win, rep
        self.links, self.apply_buttons = [], []        # (folder and release links; Apply buttons, for tests)
        self.setMinimumSize(640, 420)
        self.resize(1040, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        box = heading(heading_words(rep), (note_words(rep["note"]) + "\n" if rep.get("note") else "") + self.counts())
        self.head, self.hint = box.itemAt(0).widget(), box.itemAt(1).widget()
        lay.addLayout(box)
        self.tree = QTreeWidget()
        self.tree.setObjectName("reportTree")
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels([tr(c) if c else "" for c in COLUMNS])
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideRight)          # (the whole issue: below, for the selected row)
        self.tree.setAccessibleName(tr("Report"))
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        for c in (COL_FOLDER, COL_ISSUE):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
        for c in (COL_ALBUM, COL_RELEASE, COL_APPLY):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        hdr.resizeSection(COL_ALBUM, 220)
        hdr.resizeSection(COL_RELEASE, 220)
        hdr.resizeSection(COL_APPLY, 150)
        lay.addWidget(self.tree, 1)
        self.detail = QLabel()                             # (the selected album's issue, whole)
        self.detail.setTextFormat(Qt.PlainText)           # (tag text as it is, never as markup)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setAccessibleName(tr("Issue"))
        self.detail.setMinimumHeight(self.detail.fontMetrics().height() * 2)
        lay.addWidget(self.detail)
        self.tree.currentItemChanged.connect(self.show_detail)
        row = QHBoxLayout()
        self.html_btn = QPushButton(tr("Export as Web Page…"))
        self.csv_btn = QPushButton(tr("Export as CSV…"))
        self.html_btn.clicked.connect(lambda: self.export("html"))
        self.csv_btn.clicked.connect(lambda: self.export("csv"))
        close = QPushButton(tr("Close"))
        close.setDefault(True)
        close.setProperty("role", "primary")
        close.clicked.connect(self.accept)
        for b in (self.html_btn, self.csv_btn):
            b.setAutoDefault(False)
            b.setAccessibleName(b.text().rstrip("…"))
            row.addWidget(b)
        row.addStretch(1)
        close.setAccessibleName(tr("Close"))
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def counts(self) -> str:
        c = self.rep["counts"]
        return " · ".join(f"{tr(self.section_words(k))}: {c[k]}" for k in ("decide", "failed", "done"))

    def section_words(self, key) -> str:
        return SECTION_WORDS["ready" if key == "done" and not self.rep.get("applied") else key]

    def fill(self):
        self.tree.clear()
        self.links, self.apply_buttons, self.row_links = [], [], {}
        for key in ("decide", "failed", "done"):
            rows = [r for r in self.rep["rows"] if r["section"] == key]
            top = QTreeWidgetItem([f"{tr(self.section_words(key))} ({len(rows)})"])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(top)
            if not rows:
                none = QTreeWidgetItem([tr("Nothing.")])
                none.setFirstColumnSpanned(True)
                top.addChild(none)
            for r in rows:
                self.add_row(top, r)
            top.setExpanded(True)

    def add_row(self, top, r):
        it = QTreeWidgetItem([r["album"], "", r["issue"]])
        it.setToolTip(COL_ALBUM, plain_tip(f"{r['album']} ({status_words(r['status'])})"))
        it.setToolTip(COL_ISSUE, plain_tip(r["issue"]))
        it.setData(0, Qt.UserRole, r)
        top.addChild(it)
        folder = _link(short_path(r["folder"]), r["uri"],
                       tr("Open {folder} in the file manager").format(folder=r["folder"]))
        self.tree.setItemWidget(it, COL_FOLDER, folder)
        self.links.append(folder)
        self.row_links[id(it)] = [folder]
        for i, c in enumerate(r["candidates"]):
            line = it if i == 0 else QTreeWidgetItem(["", "", ""])
            if i:
                it.addChild(line)
            import report
            words = f"{report.candidate_words(c)} ({'MusicBrainz' if c['source'] == 'musicbrainz' else 'Discogs'})"
            lab = _link(words, c["url"])
            if c.get("lengths") is False:
                lab.setToolTip(tr("This release lists no track lengths: check it is your copy before applying it."))
            self.tree.setItemWidget(line, COL_RELEASE, lab)
            self.links.append(lab)
            self.row_links.setdefault(id(line), []).append(lab)
            b = QPushButton(tr("Apply This Release…"))
            b.setAccessibleName(tr("Apply this release to {album}").format(album=r["album"]))
            b.setToolTip(tr("Preview the album with this release; nothing is written until you apply"))
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, row=r, cand=c: self.apply(row, cand))
            self.tree.setItemWidget(line, COL_APPLY, b)
            self.apply_buttons.append(b)
        it.setExpanded(True)

    def show_detail(self, item, old=None):
        # (a selected row's links in the selection's text colour: never teal on teal)
        for x, on in ((old, False), (item, True)):
            for lab in self.row_links.get(id(x), []) if x is not None else []:
                lab.setText(_link_html(lab.property("words"), lab.property("url"),
                                       self.palette().color(QPalette.HighlightedText).name() if on else ""))
        r = item.data(0, Qt.UserRole) if item is not None else None
        while r is None and item is not None and item.parent() is not None:   # (a further release's line)
            item = item.parent()
            r = item.data(0, Qt.UserRole)
        self.detail.setText(f"{r['album']}: {r['issue']}\n{r['folder']}" if isinstance(r, dict) else "")

    def apply(self, row, cand) -> bool:
        """Preview the album with this release on the Overview (never written from here)."""
        ov = self.win.pages.get("Overview") if self.win is not None else None
        if ov is None:
            return False
        if getattr(ov, "running", False):
            self.win.statusBar().showMessage(tr("Finish or cancel the current work first."), 4000)
            return False
        files = row.get("files") if row.get("split") else None
        if ov.preview_release(row.get("apply_folder") or row["source_dir"], cand["ref"], files, only=bool(files)):
            self.accept()
            return True
        return False

    def export(self, kind):
        import report
        name = f"tagmender-report-{self.rep.get('job') or 'albums'}.{'html' if kind == 'html' else 'csv'}"
        path = sheets.save_file(self, tr("Export the Report"), name,
                                tr("Web pages (*.html)") if kind == "html" else tr("CSV files (*.csv)"))
        if not path:
            return ""
        try:
            report.save(self.rep, path, kind)
        except (OSError, ValueError) as exc:
            sheets.warning(self, tr("The report could not be saved."), str(exc))
            return ""
        if self.win is not None:
            self.win.statusBar().showMessage(tr("Report saved to {path}").format(path=path), 8000)
        return path


def open_report(win, rep) -> ReportSheet:
    dlg = ReportSheet(win, rep)
    if sheets.blocking():
        dlg.open()                                      # (window-modal; Apply hands over to the Overview)
    else:
        sheets.shown.append(("report", dlg.windowTitle(), ""))
    win._report_sheet = dlg                             # (kept: one at a time)
    return dlg


def for_window(win):
    """The report of what the page shown has (a job selected under Jobs, the Overview's preview,
    the albums found under Needs attention), opened as a sheet once it is built; None when there
    is nothing to report."""
    import report
    page = win.stack.currentWidget()
    jobs, ov, att = (win.pages.get(n) for n in ("Jobs", "Overview", "Needs attention"))
    if page is jobs and jobs.job():
        job = jobs.job()
        work = lambda _w: report.for_job(job)  # noqa: E731
    elif page is ov and ov.plan:
        plan = ov.plan
        work = lambda _w: report.build(plan)  # noqa: E731
    elif page is att and att.rows:
        rows = list(att.rows)
        work = lambda _w: report.for_attention(rows)  # noqa: E731
    else:
        return None

    def failed(msg):
        sheets.warning(win, tr("The report could not be made."), msg.splitlines()[0] if msg else "")
    return start(page, work, lambda rep: open_report(win, rep), failed=failed)


def can_report(win) -> bool:
    page = win.stack.currentWidget() if hasattr(win, "stack") else None
    jobs, ov, att = (win.pages.get(n) for n in ("Jobs", "Overview", "Needs attention"))
    return bool((page is jobs and jobs is not None and jobs.job()) or
                (page is ov and ov is not None and ov.plan and not getattr(ov, "running", False)) or
                (page is att and att is not None and att.rows))
