"""cover_chooser.py - Find Covers…: the album's own pictures and every cover the sources find,
side by side with their size and source, to pick one by eye (and, when an image service is
set up, AI covers made from the album's presets).

The chosen image goes to the inspector as Choose Image… sends one: checked, staged and shown as
the album's new cover; nothing is written until the preview is applied, and Undo puts it back."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from . import sheets
from .i18n import tr
from .rows import caption
from .work import start

KIND_WORDS = {"own": "Yours", "found": "Found", "ai": "Made"}


class CoverChooser(QDialog):
    def __init__(self, inspector, album):
        super().__init__(inspector.window())
        self.inspector, self.album = inspector, album
        from .common import album_words
        name = album_words(album.get("source_dir", ""))
        sheets.prepare(self, tr("Covers for {0}").format(name))
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        head = QLabel(tr("Covers for {0}").format(name))
        head.setProperty("role", "headline")
        lay.addWidget(head)
        lay.addWidget(caption(tr("Your own pictures first, then what the cover sources found, largest first. "
                                 "Pick one: it becomes the new cover in the preview, and Undo puts the old one "
                                 "back."), "hint"))
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(160, 160))
        self.grid.setGridSize(QSize(190, 230))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(6)
        self.grid.setAccessibleName(tr("Covers"))
        self.grid.itemDoubleClicked.connect(lambda _it: self.use())
        self.grid.currentItemChanged.connect(lambda *_: self._enable())
        lay.addWidget(self.grid, 1)
        self.status = caption("", "hint")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.more = QPushButton(tr("Search Online"))
        self.more.setAccessibleName(tr("Look for more covers with the cover sources"))
        self.more.setAutoDefault(False)
        self.more.clicked.connect(self.search)
        row.addWidget(self.more)
        self.ai = QPushButton(tr("Make AI Covers…"))
        self.ai.setAccessibleName(tr("Make covers with your AI image service"))
        self.ai.setAutoDefault(False)
        self.ai.clicked.connect(self.make_ai)
        row.addWidget(self.ai)
        row.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.use_btn = QPushButton(tr("Use This Cover"))
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(self.use)
        row.addWidget(self.use_btn)
        lay.addLayout(row)
        self.choices = []
        self.searching = False
        self._ai_ready()
        self._enable()
        self.load_own()

    # ---- filling ------------------------------------------------------------------------------
    def add(self, choices):
        for c in choices:
            self.choices.append(c)
            it = QListWidgetItem(self._words(c))
            if c.thumb:
                pm = QPixmap()
                pm.loadFromData(c.thumb)
                it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, len(self.choices) - 1)
            it.setToolTip(f"{c.source}\n{c.width}×{c.height}" + (f"\n{c.note}" if c.note else ""))
            it.setData(Qt.AccessibleTextRole, self._words(c).replace("\n", ", "))
            self.grid.addItem(it)
        if self.grid.currentItem() is None and self.grid.count():
            self.grid.setCurrentRow(0)
        self._enable()

    @staticmethod
    def _words(c) -> str:
        size = f"{c.width}×{c.height}" if c.width else tr("size unknown")
        return f"{size}\n{tr(KIND_WORDS.get(c.kind, ''))}: {c.source}"

    def load_own(self):
        import covers_find
        folder = self.album.get("source_dir", "")
        files = [fe["src"] for fe in self.album.get("files") or []]
        self.status.setText(tr("Reading your own pictures…"))

        def done(got):
            self.add(got)
            self.status.setText(tr("{0} of your own. Search Online looks for more.").format(len(got)) if got
                                else tr("No pictures of your own. Search Online looks for covers."))
        start(self, lambda _w: covers_find.own(folder, files), done, failed=self._failed)

    def search(self):
        import covers_find
        if self.searching:
            return
        self.searching = True
        self.more.setEnabled(False)
        self.status.setText(tr("Asking the cover sources…"))
        album = self.album
        folder = album.get("source_dir", "")
        files = [fe["src"] for fe in album.get("files") or []]

        def work(w):
            ids, artist, title = covers_find.ids_of(album)
            return covers_find.found(folder, files, ids, artist, title, cancelled=lambda: w.cancelled)

        def done(got):
            self.searching = False
            self.add(got)
            self.status.setText(tr("{0} found online.").format(len(got)) if got else
                                tr("The cover sources found no front cover for this album."))
        start(self, work, done, failed=self._failed)

    def _failed(self, msg):
        self.searching = False
        self.more.setEnabled(True)
        self.status.setText(tr("That did not work: {0}").format(msg.split("\n\n")[0]))

    # ---- AI covers ----------------------------------------------------------------------------
    def _ai_ready(self):
        try:
            import covers_ai
            why = covers_ai.not_ready()
        except ImportError:
            why = tr("not available in this version")
        self.ai_missing = why                             # (clickable all the same: it says what to set up)
        self.ai.setEnabled(True)
        self.ai.setToolTip(tr("Needs: {0}").format(why) if why else
                           tr("One cover per preset: Faithful, Creative, Art and Era"))

    def make_ai(self):
        if getattr(self, "ai_missing", ""):
            ans = sheets.ask(self, tr("AI covers need an image service"),
                             tr("AI covers are made by an image service of your own, such as OpenAI's "
                                "(platform.openai.com/api-keys): paste its key under Preferences › Keys › AI "
                                "covers. Each cover is a request it may charge for. A Tavily key (Keys › Web "
                                "search) adds the Creative and Art presets, which need reference images."),
                             buttons=(tr("Cancel"), tr("Open Preferences")), default=tr("Open Preferences"))
            if ans == tr("Open Preferences"):
                win = self.inspector.window()
                if hasattr(win, "preferences"):
                    self.reject()
                    prefs = win.preferences()
                    if hasattr(prefs, "show_tab"):
                        prefs.show_tab("Keys")
            return
        import covers_ai
        import covers_find
        _ids, artist, title = covers_find.ids_of(self.album)
        own = [c for c in self.choices if c.kind == "own"]
        plan = covers_ai.plan(self.album, artist, title, own)
        body = "<ul>" + "".join(f"<li>{tr(p['preset_name'])}: {tr(p['what'])}</li>" for p in plan) + "</ul>"
        if not sheets.confirm(self, tr("Make {0} AI covers?").format(len(plan)),
                              tr("<p>Each is a request to your image service, which may charge for it:</p>") + body,
                              tr("The covers are only shown here; one is used only when you pick it."),
                              tr("Make Covers")):
            return
        self.ai.setEnabled(False)
        self.status.setText(tr("Making covers…"))

        def work(w):
            return covers_ai.make(plan, progress=w.progress.emit, cancelled=lambda: w.cancelled)

        def done(got):
            self.add(got)
            self._ai_ready()
            self.status.setText(tr("{0} covers made.").format(len(got)))
        start(self, work, done, progress=lambda text: self.status.setText(str(text)), failed=self._failed)

    # ---- choosing -----------------------------------------------------------------------------
    def chosen(self):
        it = self.grid.currentItem()
        return self.choices[it.data(Qt.UserRole)] if it is not None else None

    def _enable(self):
        self.use_btn.setEnabled(self.chosen() is not None)

    def use(self):
        c = self.chosen()
        if c is None:
            return
        label = c.source if c.kind != "ai" else tr("AI cover ({0})").format(c.source)
        # (an AI cover carries CoverUp's "generated" mark in its JPEG comment: a real one replaces it later)
        self.inspector._fetch(label, c.full, f"chosen:{c.kind}:{c.source}")
        self.accept()


def open_chooser(inspector):
    album = inspector.album
    if album is None or not inspector.can_edit(album):
        return
    dlg = CoverChooser(inspector, album)
    sheets.run(dlg, "covers")
