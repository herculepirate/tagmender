"""gui/inspector.py - The inspector (Ctrl+Alt+I): the selected album's tags, artwork and evidence.

A right-hand dock. Its header names the album and its status (icon and word) with Open folder,
Search the web and Use another release…; three tabs below:
  Tags      Tag | Original | New for one file or all, changes first; each value can be edited,
            reverted to the original, removed, and tags added (Picard's metadata box)
  Artwork   the album's own cover beside the new one, with size, KB and source; an image file or
            link dropped there becomes the new cover (Picard's cover art box)
  Evidence  score and margin in words, source, what matched, doubts, where each tag came from, codes,
            and the full preview text (monospace) folded away
Images are read as QImage on a worker and become pixmaps on the UI thread.

Editing never writes a file: each edit is kept in Inspector.edits ({album folder: [edit]}, the
shape plan.apply_edits reads) and applied to the plan shown, on a worker; Apply then writes it
as any planned change (staged, verified, journaled, undoable). For the app's window to keep the
edits when it previews again: engine.Engine.edits = inspector.edits_for_preview(); a new folder:
inspector.forget_edits(). Inspector.edited(album) says an album of the plan changed (its row,
the summary and the Apply button may need refreshing)."""
from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog, QDockWidget, QFileDialog,
                               QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QStackedWidget, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons, sheets, style
from .common import (album_words, label_for, left_header, plural, planmod, popup_menu, reasons, reduced_motion,
                     say)
from .i18n import N_, tr
from .tagnames import NAMES
from .tagnames import display as tag_name
from .widgets import EmptyState
from .work import start

TABS = ("Tags", "Artwork", "Evidence")
CHANGE = {"added": ("+", N_("Added")), "changed": ("~", N_("Changed")), "removed": ("−", N_("Removed")),
          "preserved": ("=", N_("Kept")), "locked": ("=", N_("Locked")), "same": ("=", N_("Unchanged")),
          "kept (your edit)": ("=", N_("Kept"))}
# the change column's Lucide icon for each symbol (a typed glyph's weight depends on the font), and
# a value you edited: a pencil (its italics are the second cue; the word is read out and shown below)
CHANGE_ICON = {"+": "plus", "~": "arrow-right", "−": "minus", "=": "equal"}
YOURS_ICON = "pencil"
YOURS_WORDS = N_("(your edit)")                          # after the value of a row you edited
NONE = "(none)"                                      # a tag without a value, in a cell
EDIT_ROLE = Qt.UserRole + 1                          # a row's New value as an inline editor starts with it
IMAGE_KINDS = ("JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "MPO")   # (PIL's names; MPO: a camera's JPEG)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff")
MIN_SIDE = 100                                       # a cover you choose: at least this many pixels a side
COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
PAD = 16                                             # the inspector's content margins (the 8-pt grid)
TAG_COL = 130                                        # the Tag column's widest (names past it are elided)
TAG_COL_WIDE = 220                                   # ... in a wide inspector: a quarter of it, at most this
VALUE_MIN = 120                                      # stacked, the values' column keeps at least this
WRAP_UNDER = 340                                     # narrower than this, values wrap instead of being cut
ROW_AIR = 8                                          # added to a wrapped row: its lines breathe (the 8-pt grid)
CELL_PAD = 2 * 8 + 2 * 3 + 2                         # a cell's padding (style.py), Qt's text margins and a pixel each side
# the Evidence sections, in order (the keys of EvidenceView.fields)
SECTIONS = (N_("Score"), N_("Source"), N_("What matched"), N_("Doubts"), N_("Where each tag came from"), N_("Codes"))


class OneLine(QStyledItemDelegate):
    """A column kept on one line (shortened at the end) in a table that wraps its other columns."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.features &= ~QStyleOptionViewItem.WrapText


def _words(vals):
    return "; ".join(str(v) for v in vals or []) or NONE


class NewValue(QStyledItemDelegate):
    """Typing in the table edits the New value only: side by side its own cell, stacked the
    "Original → New" cell, whose editor starts with the New value (EDIT_ROLE)."""

    def __init__(self, view):
        super().__init__(view.table)
        self.view = view

    def setEditorData(self, editor, index):
        editor.setText(index.data(EDIT_ROLE) or "")

    def setModelData(self, editor, model, index):
        self.view.typed_row(index.row(), editor.text())


def _label(text="", role=None, wrap=True, select=True, links=False):
    """Text that can be selected with the mouse (not a Tab stop); with links=True a link can be
    reached with Tab and opened with Enter too."""
    lb = QLabel(text)
    lb.setWordWrap(wrap)
    if role:
        lb.setProperty("role", role)
    if links:
        lb.setTextInteractionFlags(Qt.TextBrowserInteraction)
    elif select:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def score_word(conf, plan) -> str:
    th = ((plan or {}).get("profile_data") or {}).get("thresholds") or {}
    if conf is None:
        return ""
    if conf >= th.get("organize_min_confidence", 95):
        return tr("Sure")
    if conf >= th.get("auto_min_confidence", 90):
        return tr("Likely")
    return tr("Unsure")


def internal_name(text: str) -> str:
    """A tag's name as typed ("Catalog Number" or "catalognumber") as Picard names it; a name
    of your own stays as you spelled it ("MyTag")."""
    t = (text or "").strip()
    by_display = {v.lower(): k for k, v in NAMES.items()}
    return by_display.get(t.lower()) or (t.lower() if t.lower() in NAMES else t)


def bad_name(name: str) -> str:
    """Why a tag name cannot be used, or ""."""
    if not name:
        return tr("Type the tag's name.")
    if name.startswith("~") or name.startswith("_") or "=" in name or any(ord(c) < 32 for c in name):
        return tr("“{name}” cannot be a tag's name: it may not start with ~ or _, or hold = or control "
                  "characters.").format(name=name)
    return ""


def tag_sheet(parent, headline, body="", name="", values=(), ask_name=False, action=None):
    """A sheet to type a tag's values, one per line (a tag may hold several: two artists, two
    genres), and with ask_name its name: (name, [values]), or None for Cancel. Offscreen:
    recorded in sheets.shown, None. Patch it as gui.inspector.tag_sheet."""
    action = action or tr("Save")
    s = sheets.Sheet(parent, headline, body, "pencil", "accent")
    field_ = None
    if ask_name:
        field_ = QLineEdit(name)
        field_.setAccessibleName(tr("Tag name"))
        field_.setPlaceholderText(tr("Tag name, as the MusicBrainz tag mapping names it (Catalog Number, Mood …)"))
        comp = QCompleter(sorted(NAMES.values()), field_)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        field_.setCompleter(comp)
        s.extra.addWidget(field_)
    text = QPlainTextEdit("\n".join(str(v) for v in values))
    text.setAccessibleName(tr("Values, one per line"))
    text.setPlaceholderText(tr("One value per line"))
    text.setTabChangesFocus(True)
    text.setFixedHeight(96)
    s.extra.addWidget(text)
    s.add_button(tr("Cancel"), accept=False)
    s.add_button(action, default=True)
    if not sheets.blocking():
        sheets.shown.append(("tag", headline, body))
        s.deleteLater()
        return None
    (field_ or text).setFocus()
    ok = sheets.run(s, "tag") == QDialog.Accepted
    if not ok:
        return None
    vals = [v.strip() for v in text.toPlainText().splitlines() if v.strip()]
    return (field_.text().strip() if field_ else name), vals


def source_words(row, album) -> str:
    """Where a row's new value comes from, in words (the match's provenance, or your edit)."""
    if row.get("by") == planmod.BY_YOU:
        return tr("Your edit")
    if row["action"] in ("same", "preserved", "locked") or row["action"].startswith("kept"):
        return tr("The file's own value")
    if row.get("note_kind") in ("replaced", "removed"):   # (plan._release_id_notes: an ID of yours)
        words = (tr("Replaces your {db} release ID {id}") if row["note_kind"] == "replaced"
                 else tr("Removes your {db} release ID {id}"))
        return words.format(db=row.get("note_db") or "", id=row.get("note_id") or "")
    if row.get("note"):                  # (a plan saved before note_kind: its English words)
        return str(row["note"])[:1].upper() + str(row["note"])[1:]
    if row["action"] == "removed":
        return tr("Removed by the profile")
    src = ((album or {}).get("identification") or {}).get("provenance", {}).get(row["field"])
    if src:
        return "From " + str(src).replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")
    if row["field"].startswith("tagmender"):
        return "TagMender's mark"
    prov = (((album or {}).get("identification") or {}).get("provider") or "").split("+")[0]
    return ("From " + prov.replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")) if prov \
        else tr("From the profile")


# the Tags tab's buttons (key, words, icon, method) and their keys (the Album > Tag commands' shortcuts)
BUTTONS = (("edit", N_("Edit Value…"), "pencil", "edit_selected"),
           ("revert", N_("Revert to Original"), "history", "revert_selected"),
           ("suggested", N_("Use Suggested Value"), "sparkles", "use_suggested"),
           ("add", N_("Add Tag…"), "plus", "add_tag"),
           ("remove", N_("Remove Tag"), "minus", "remove_selected"))
BUTTON_KEYS = {"edit": "F2", "revert": "Ctrl+Alt+R", "suggested": "Ctrl+Alt+U", "add": "Ctrl+Alt+A",
               "remove": "Delete"}
# what each button does, in its tooltip (icon-only buttons: the words tell the two arrows apart)
BUTTON_TIPS = {"revert": N_("the file's own value"),
               "suggested": N_("forget your edit: the value the preview suggested")}
PAIR = ("add", "remove")
UNDO_WORDS = N_("Edit > Undo Edit (Ctrl+Z)")                             # (at the right, as Finder's + and − pair)
# the commands of gui.actions behind them, in the table's menu order
def _rating_tip() -> str:
    """What your stars do: sent by Album > Submit Ratings…, and written into the files by Apply once
    the tag writer can write them (tagio.WRITES_RATING)."""
    try:
        import tagio
        writes = bool(getattr(tagio, "WRITES_RATING", False))
    except Exception:
        writes = False
    return (tr("Your rating of the track: written into the files when you apply (POPM, RATING), and sent to "
               "MusicBrainz only with Album > Submit Ratings…") if writes else
            tr("Your rating of the track, kept with your edits: Album > Submit Ratings… sends it to MusicBrainz. "
               "It is not written into the files yet."))


RATING_TIP = _rating_tip()
TAG_COMMANDS = ("edit_tag", "revert_tag", "use_suggested", "remove_tag", None, "add_tag")
TAG_BUTTON = {"edit_tag": "edit", "revert_tag": "revert", "use_suggested": "suggested", "remove_tag": "remove",
              "add_tag": "add"}


class TagsView(QWidget):
    """Tag | Original | New, the changes first. With an owner (the Inspector) the New values
    can be edited: Edit…, Revert to Original, Add Tag…, Remove Tag (and in the table: a double
    click, or F2, on a value). Each edit is one of your edits of the album's plan."""

    def __init__(self, owner=None):
        super().__init__()
        self.owner = owner
        lay = QVBoxLayout(self)
        lay.setContentsMargins(PAD, 12, PAD, 12)
        self.files = QComboBox()
        self.files.setAccessibleName(tr("File to show the tags of"))
        self.files.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.files.setMinimumContentsLength(12)
        self.files.currentIndexChanged.connect(self.fill)
        self.files.currentIndexChanged.connect(lambda _i: self.show_rating())
        lay.addWidget(self.files)
        self.rating = None                                   # Picard's rating stars (the file shown, or all)
        if owner is not None:
            from .account import RatingBox
            self.rating = RatingBox(owner)
            self.rating.setAccessibleName(tr("Rating"))
            self.rating.setToolTip(RATING_TIP)                # (the stars' own tip says how many)
            self.rating.setVisible(False)
            lay.addWidget(self.rating)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", tr("Tag"), tr("Original"), tr("New")])
        self.table.horizontalHeaderItem(0).setToolTip(tr("Change: plus added, arrow changed, minus removed, "
                                                         "equals kept, pencil your edit"))
        self.table.horizontalHeaderItem(0).setData(Qt.AccessibleTextRole,
                                                   tr("Change"))   # (no word shown: an icon column)
        self.table.setAccessibleName(tr("Tags: original and new values"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideRight)          # (a value reads from its start; full in the tooltip)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.itemChanged.connect(self.typed)
        self.table.itemDoubleClicked.connect(self.double_clicked)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.installEventFilter(self)                  # (Return edits, Delete removes: keys())
        self.new_value = NewValue(self)
        for c in (2, 3):
            self.table.setItemDelegateForColumn(c, self.new_value)
        left_header(self.table)
        h = self.table.horizontalHeader()                    # (the New column is always in view)
        h.setMinimumSectionSize(24)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 28)
        h.setSectionResizeMode(1, QHeaderView.Interactive)  # (fitted to the names, at most TAG_COL)
        h.resizeSection(1, TAG_COL)
        self.one_line = OneLine(self.table)                 # (a tag's name never wraps: "Catalog Num…")
        self.table.setItemDelegateForColumn(1, self.one_line)
        self.row_timer = QTimer(self)                       # rows fitted once the columns have their widths
        self.row_timer.setSingleShot(True)
        self.row_timer.setInterval(0)
        self.row_timer.timeout.connect(self.fit_rows)
        h.sectionResized.connect(lambda i, _old, _new: i == 2 and self.row_timer.start())
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.source = _label("", "caption")                  # where the selected row's new value comes from
        lay.addWidget(self.source)
        btns = QHBoxLayout()
        btns.setSpacing(4)
        self.buttons = {}
        for key, text, icon, fn in BUTTONS:
            fn, text = getattr(self, fn), tr(text)
            b = QToolButton()
            b.setProperty("role", "tool")
            b.setText(text)
            tip = f"{text}: {tr(BUTTON_TIPS[key])}" if key in BUTTON_TIPS else text
            b.setToolTip(f"{tip} ({BUTTON_KEYS[key]})" if key in BUTTON_KEYS else tip)
            b.setAccessibleName(text.rstrip("…"))
            b.setIcon(icons.icon(icon, "ink"))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)       # (one style for the row; words in the tooltip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.clicked.connect(lambda _=False, f=fn: f())
            if key == PAIR[0]:
                btns.addStretch(1)
            btns.addWidget(b)
            self.buttons[key] = b
        lay.addLayout(btns)
        self.note = _label("", "hint")
        lay.addWidget(self.note)
        self.album = None
        self.current = {}                                    # file -> its tags as read (on a worker)
        self.shown = []                                      # the rows as listed
        self.filling = False
        self.update_buttons()

    def retint(self):
        for key, _text, icon, _fn in BUTTONS:
            self.buttons[key].setIcon(icons.icon(icon, "ink"))
        if self.shown:
            self.fill()                                      # (the change icons in the new colours)

    def show_album(self, album, keep=False):
        same = keep and album is self.album
        pick = self.files.currentIndex() if same else 0
        if not same:
            self.current = {}
        self.album = album
        self.files.blockSignals(True)
        self.files.clear()
        self.files.addItem(tr("All files"), None)
        for i, fe in enumerate((album or {}).get("files") or []):
            self.files.addItem(Path(fe["src"]).name, i)
        self.files.setCurrentIndex(pick if 0 <= pick < self.files.count() else 0)
        self.files.blockSignals(False)
        self.fill()
        self.show_rating()
        if album and not same:
            self.read_tags(album)

    def show_rating(self):
        """The rating row: for an album with files (the file shown, or all of them)."""
        if self.rating is None:
            return
        files = (self.album or {}).get("files") or []
        self.rating.setVisible(bool(files))
        if files:
            self.rating.show_album(self.album, self.file_src())

    def read_tags(self, album):
        """Every tag the files have (the ones staying as they are too), read on a worker."""
        files = [fe["src"] for fe in album.get("files") or []][:500]
        if not files or self.owner is None:
            return

        def work(_w):
            import tagio
            out = {}
            for f in files:
                try:
                    out[f] = tagio.read(f)
                except Exception:
                    continue
            return out

        def done(got):
            if self.album is album:
                self.current = got
                self.fill()
        start(self.owner, work, done, failed=lambda _m: None)

    # ---- the rows ---------------------------------------------------------
    def chosen(self):
        files = (self.album or {}).get("files") or []
        i = self.files.currentData()
        return files if i is None else [files[i]] if 0 <= i < len(files) else []

    def file_src(self):
        """The file shown (None: all of them)."""
        i = self.files.currentData()
        files = (self.album or {}).get("files") or []
        return files[i]["src"] if i is not None and 0 <= i < len(files) else None

    def rows(self):
        i = self.files.currentData()
        out, seen = [], set()
        for fe in self.chosen():
            rows = list(fe.get("diff") or [])
            named = {r["field"] for r in rows}
            for k, v in sorted((self.current.get(fe["src"]) or {}).items()):
                if not k.startswith("~") and k not in named and k not in (fe.get("changes") or {}):
                    rows.append({"field": k, "before": list(v or []), "after": list(v or []), "action": "same"})
            for r in rows:
                key = (r["field"], tuple(r.get("before") or []), tuple(r.get("after") or []), r["action"])
                if i is None and key in seen:                   # (all files: one row per distinct change)
                    continue
                seen.add(key)
                out.append(r)
        change = lambda r: r["action"] in ("added", "changed", "removed")  # noqa: E731
        return sorted(out, key=lambda r: (not change(r), r.get("by") != planmod.BY_YOU, r["field"]))

    def editable(self) -> bool:
        return self.owner is not None and not self.owner.busy() and bool((self.album or {}).get("files")) and \
            (self.album or {}).get("status") in planmod.EDITABLE

    def fill(self, *_):
        """Wide: Tag | Original | New. Narrow (under WRAP_UNDER): New under Original in one
        column, so a date or a label is never cut to "199…" (the whole value is in the tooltip)."""
        rows = self.rows()
        self.shown = rows
        self.stacked = self.width() < WRAP_UNDER
        t = self.table
        self.filling = True
        t.blockSignals(True)
        t.setColumnHidden(3, self.stacked)
        t.horizontalHeaderItem(2).setText(tr("Original → New") if self.stacked else tr("Original"))
        t.setWordWrap(self.stacked)
        t.setRowCount(len(rows))
        inline = self.editable()                             # (stacked too: the New line is edited)
        for n, r in enumerate(rows):
            sym, word = CHANGE.get(r["action"], ("·", r["action"].capitalize()))
            word = tr(word)
            before, after = _words(r.get("before")), _words(r.get("after"))
            change = r["action"] in ("added", "changed", "removed")
            mine = r.get("by") == planmod.BY_YOU
            src = source_words(r, self.album)
            both = (f"{before}\n→ {after}" if change else after) if self.stacked else before
            # (your edit said in words on its row too, not by the pencil and italics alone)
            cells = (sym, tag_name(r["field"]), both + (f"\n{tr(YOURS_WORDS)}" if mine and self.stacked else ""),
                     after + (f"  {tr(YOURS_WORDS)}" if mine else ""))
            for j, v in enumerate(cells):
                it = QTableWidgetItem(v)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setToolTip(word if j == 0 else f"{v} ({r['field']})" if j == 1 else
                              tr("Original: {before}\nNew: {after}\n{src}").format(before=before, after=after,
                                                                                   src=src) if j == 2 and self.stacked
                              else
                              f"{v}\n{src}" if j == 3 else v)
                if j == 0:                                   # (an icon in colour, the word read out)
                    it.setText("")
                    role = ("ready" if r["action"] in ("added", "changed") else
                            "failed" if r["action"] == "removed" else "muted")
                    it.setIcon(icons.icon(YOURS_ICON if mine else CHANGE_ICON.get(sym, "equal"),
                                          "accent" if mine else role, 14))
                    words = tr("{word}, your edit").format(word=word) if mine else word
                    it.setToolTip(tr("{word} · your edit").format(word=word) if mine else word)
                    it.setData(Qt.AccessibleTextRole, words)
                    it.setData(Qt.UserRole, word)
                elif j == 1:
                    it.setData(Qt.UserRole, r["field"])
                elif j == 2 and self.stacked:
                    it.setData(Qt.AccessibleTextRole, f"Original {before}, new {after}" if change else after)
                if j == (2 if self.stacked else 3):
                    if j == 3:
                        it.setData(Qt.UserRole, src)
                    it.setData(EDIT_ROLE, "; ".join(str(v) for v in r.get("after") or []))
                    if inline and len(r.get("after") or []) <= 1:    # (several values: Edit Value…)
                        it.setFlags(it.flags() | Qt.ItemIsEditable)
                if mine and j in (1, 2, 3):                  # (your edits in italics, and said so)
                    f = it.font()
                    f.setItalic(True)
                    it.setFont(f)
                t.setItem(n, j, it)
        t.blockSignals(False)
        self.filling = False
        n_changes = sum(1 for r in rows if r["action"] in ("added", "changed", "removed"))
        n_mine = sum(1 for r in rows if r.get("by") == planmod.BY_YOU)
        undo = self.owner is not None and hasattr(self.owner, "can_undo_edit") and self.owner.can_undo_edit()
        self.note.setText((tr("{0}, then the tags that stay as they are.").format(plural(n_changes, tr('change'),
                                                                                         tr('changes')))
                           + (tr(" {0} edited by you.").format(plural(n_mine, tr('value'),
                                                                      tr('values'))) if n_mine else "")
                           + (tr(" {UNDO_WORDS} takes back the last one.")
                              .format(UNDO_WORDS=tr(UNDO_WORDS)) if undo else "")) if rows
                          else tr("No tag changes for this album."))
        self.fit()
        self.selection_changed()

    # ---- editing ------------------------------------------------------------
    def selected_row(self):
        r = self.table.currentRow()
        if not self.table.selectionModel().hasSelection() or not 0 <= r < len(self.shown):
            return None
        return self.shown[r]

    def selection_changed(self):
        """The selected row's change in words, and where its new value comes from:
        "Changed · your edit", "Added · from Discogs"."""
        row = self.selected_row()
        w = source_words(row, self.album) if row else ""
        word = tr(CHANGE.get(row["action"], ("", row["action"].capitalize()))[1]) if row else ""
        say(self.source, tr("New value"), f"{word} · {w[:1].lower()}{w[1:]}" if row else "")
        self.update_buttons()

    def update_buttons(self):
        on, row = self.editable(), self.selected_row()
        self.buttons["add"].setEnabled(on)
        for k in ("edit", "revert", "remove"):
            self.buttons[k].setEnabled(on and row is not None)
        if row is not None:
            self.buttons["revert"].setEnabled(on and (row.get("before") != row.get("after")
                                                      or row.get("by") == planmod.BY_YOU))
            self.buttons["remove"].setEnabled(on and bool(row.get("after")))
        self.buttons["suggested"].setEnabled(on and row is not None and row.get("by") == planmod.BY_YOU)
        win = getattr(self.owner, "win", None)
        if win is not None and hasattr(win, "update_tag_actions"):
            win.update_tag_actions()                         # (the Album > Tag commands follow)

    def select_field(self, field_):
        for n, r in enumerate(self.shown):
            if r["field"] == field_:
                self.table.selectRow(n)
                return True
        return False

    def _set(self, field_, value):
        if self.editable():
            self.owner.set_tag(field_, value, self.file_src())

    def typed(self, it):
        """A value put into the New column's cell."""
        if self.filling or it.column() != 3 or not 0 <= it.row() < len(self.shown):
            return
        self.typed_row(it.row(), it.text())

    def typed_row(self, n, text):
        """A value typed for the row n (empty, or "(none)": the tag removed)."""
        if not 0 <= n < len(self.shown):
            return
        r = self.shown[n]
        text = (text or "").strip()
        if text == _words(r.get("after")) or (not text and not r.get("after")):
            return
        self._set(r["field"], [text] if text and text != NONE else None)

    def double_clicked(self, it):
        if it.column() != (2 if self.stacked else 3) or not (it.flags() & Qt.ItemIsEditable):
            self.edit_selected()

    def edit_key(self):
        """F2 or Return on a row: its New value typed in place when it is one value, else Edit
        Value… (several values, or none can be typed there)."""
        row = self.selected_row()
        if row is None or not self.editable():
            return
        r = self.table.currentRow()
        it = self.table.item(r, 2 if self.stacked else 3)
        if it is not None and it.flags() & Qt.ItemIsEditable:
            self.table.setCurrentItem(it)
            self.table.editItem(it)
        else:
            self.edit_selected()

    def eventFilter(self, obj, e):
        """The table's keys: Return (or Enter) edits the value, Delete or Backspace removes the tag."""
        from PySide6.QtCore import QEvent
        if obj is self.table and e.type() == QEvent.KeyPress and self.table.state() != QAbstractItemView.EditingState:
            if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2) and not e.modifiers():
                self.edit_key()
                return True
            if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not e.modifiers():
                self.remove_selected()
                return True
        return super().eventFilter(obj, e)

    def edit_selected(self):
        row = self.selected_row()
        if row is None or not self.editable():
            return
        got = tag_sheet(self, tr("Edit {0}").format(tag_name(row['field'])), self._scope_words(),
                        name=row["field"], values=row.get("after") or [])
        if got is not None:
            self._set(row["field"], got[1] or None)

    def add_tag(self):
        if not self.editable():
            return
        got = tag_sheet(self, tr("Add a Tag"), self._scope_words(), ask_name=True, action=tr("Add Tag"))
        if got is None:
            return
        name = internal_name(got[0])
        why = bad_name(name) or ("" if got[1] else tr("Type at least one value."))
        if why:
            sheets.warning(self, tr("This tag cannot be added."), why)
            return
        self._set(name, got[1])

    def revert_selected(self):
        row = self.selected_row()
        if row is not None and self.editable():
            self._set(row["field"], dict(planmod.KEEP))
            self.told(tr("{0} is back to the file's own value in the preview.").format(tag_name(row['field'])))

    def remove_selected(self):
        row = self.selected_row()
        if row is not None and self.editable():
            self._set(row["field"], None)
            self.told(tr("Removed {0} from the preview.").format(tag_name(row['field'])))

    def use_suggested(self):
        row = self.selected_row()
        if row is not None and self.editable():
            self.owner.clear_tag(row["field"], self.file_src())
            self.told(tr("{0} has the suggested value again in the preview.").format(tag_name(row['field'])))

    def told(self, what):
        """A change made at once (no sheet asked first) said in the status bar, with its undo."""
        win = getattr(self.owner, "win", None)
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(tr("{what} {UNDO_WORDS} takes it back; nothing is written until you "
                                           "apply.").format(what=what, UNDO_WORDS=tr(UNDO_WORDS)), 8000)

    def _scope_words(self) -> str:
        src = self.file_src()
        return (f"For {Path(src).name} only." if src else tr("For every file of the album.")) + \
            tr(" Nothing is written until you apply the preview.")

    def context_menu(self, pos):
        """The window's Album > Tag commands (with their shortcuts), else local ones."""
        if not self.editable():
            return
        win = getattr(self.owner, "win", None)
        if win is not None and hasattr(win, "update_tag_actions") and "edit_tag" in getattr(win, "commands", {}):
            win.update_tag_actions()
            acts = [win.act(k) if k else None for k in TAG_COMMANDS]
        else:
            row = self.selected_row()
            acts = []
            for k in TAG_COMMANDS:
                if k is None:
                    acts.append(None)
                    continue
                key = TAG_BUTTON[k]
                a = QAction(tr(next(t for b, t, _i, _f in BUTTONS if b == key)), self)
                a.setEnabled(self.buttons[key].isEnabled() if row is not None or key == "add" else False)
                a.triggered.connect(lambda _=False, b=key: self.buttons[b].click())
                acts.append(a)
        self._menu = popup_menu(self, self.table, pos, acts)

    def fit(self):
        """The Tag column as wide as its names (at most TAG_COL); rows as tall as their lines."""
        if getattr(self, "stacked", None) is not None and self.stacked != (self.width() < WRAP_UNDER):
            return self.fill()                               # (crossed the width: the other layout)
        t = self.table
        t.resizeColumnToContents(1)
        # (stacked, in a narrow inspector: the values keep most of the room, the name is elided first)
        # (but the name whole when the values keep VALUE_MIN: they wrap, a name is cut)
        room = max(int((t.viewport().width() - 28) * 0.45), t.viewport().width() - 28 - VALUE_MIN)
        side = max(TAG_COL, min(TAG_COL_WIDE, (t.viewport().width() - 28) // 4))   # (wider: whole names)
        cap = side if not getattr(self, "stacked", False) else min(TAG_COL, max(80, room))
        t.horizontalHeader().resizeSection(1, min(cap, max(56, t.columnWidth(1))))
        self.fit_rows()
        self.row_timer.start()                               # (again once the columns have their widths)

    def fit_rows(self):
        """Stacked: each row as tall as its lines, and a wrapped row ROW_AIR taller, so the lines
        of one tag are told from the next; side by side: one line each."""
        t = self.table
        one = t.verticalHeader().defaultSectionSize()
        if t.wordWrap():
            # (measured here: Qt's own row fit leaves out the stylesheet's cell padding, and cut
            # the new value, the line the column is for, to "→ Northern …")
            fm = t.fontMetrics()
            w = max(24, t.columnWidth(2) - CELL_PAD)
            for r in range(t.rowCount()):
                it = t.item(r, 2)
                h = fm.boundingRect(0, 0, w, 100000, Qt.TextWordWrap, it.text() if it else "").height()
                lines = max(1, round(h / max(1, fm.lineSpacing())))
                t.setRowHeight(r, one if lines == 1 else max(one, h + 8) + ROW_AIR)
        else:
            for r in range(t.rowCount()):
                t.setRowHeight(r, one)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


def stage_cover(data: bytes, source: str) -> dict:
    """An image you chose for the album's cover, checked (a picture TagMender can embed, at
    most artwork.MAX_BYTES, at least MIN_SIDE pixels a side) and staged as a JPEG for the plan:
    {"image", "source", "width", "height", "bytes"}. Runs on a worker; raises ValueError with
    the reason in words."""
    import io

    import artwork
    import exclusions as ex
    if not data:
        raise ValueError(tr("The image is empty."))
    if len(data) > artwork.MAX_BYTES:
        raise ValueError(tr("The image is larger than {0} MB.").format(artwork.MAX_BYTES // 1_000_000))
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            kind = im.format
            im.verify()
    except Exception:
        kind = None
    if kind not in IMAGE_KINDS:
        raise ValueError(tr("That is not a picture TagMender can use: choose a JPEG, PNG, WebP, GIF, BMP or TIFF "
                            "image."))
    path, w, h = artwork._stage(data, ex.STATE_DIR / "staging" / "your-covers")
    if min(w, h) < MIN_SIDE:
        raise ValueError(tr("The image is {w} × {h} pixels: too small for a cover (at least {MIN_SIDE} on each side).")
                         .format(w=w, h=h, MIN_SIDE=MIN_SIDE))
    return {"image": str(path), "source": source, "width": w, "height": h, "bytes": path.stat().st_size}


def image_link(text: str) -> str:
    """text as an http(s) link to an image, or ""."""
    t = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    u = QUrl(t)
    return t if u.isValid() and u.scheme() in ("http", "https") and u.host() else ""


class ArtworkView(QWidget):
    """The album's own cover (its folder's cover file, else the one embedded in its first track)
    beside the new one, each with its size. An image file or an image link dropped here (or
    Choose Image…, Use Image Link…) becomes the new cover, as one of your edits of the plan;
    Use Suggested Cover drops it again."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(PAD, 12, PAD, 12)
        lay.addWidget(self.stack)
        self.empty = EmptyState("image", tr("No new cover for this album."),
                                tr("When the profile finds a cover, it is shown here beside the album's own."))
        self.stack.addWidget(self.empty)
        body = QWidget()
        col_all = QVBoxLayout(body)
        col_all.setContentsMargins(0, 0, 0, 0)
        col_all.setSpacing(12)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.sides = {}
        for key, title in (("old", tr("Original")), ("new", tr("New"))):
            col = QVBoxLayout()
            col.addWidget(_label(title, "headline", select=False))
            pic = QLabel()
            pic.setFixedSize(112, 112)
            pic.setAlignment(Qt.AlignCenter)
            pic.setAccessibleName(tr("{title} cover").format(title=title))
            col.addWidget(pic)
            info = _label("", "caption")
            col.addWidget(info)
            col.addStretch(1)
            row.addLayout(col)
            self.sides[key] = (pic, info)
        col_all.addLayout(row)
        self.hint = _label(tr("Drop an image file or an image link here to use it as the new cover."), "hint")
        col_all.addWidget(self.hint)
        self.buttons = {}
        for line in ((("find", tr("Find Covers…"), "search", self.find_covers),),    # (one a line: 280 px wide)
                     (("file", tr("Choose Image…"), "image", self.choose_file),),
                     (("url", tr("Use Image Link…"), "globe", self.ask_url),),
                     (("clear", tr("Use Suggested Cover"), "sparkles", self.clear),)):
            r = QHBoxLayout()
            r.setSpacing(4)
            for key, text, icon, fn in line:
                b = QToolButton()
                b.setProperty("role", "tool")
                b.setText(text)
                b.setToolTip(text)
                b.setAccessibleName(text.rstrip("…"))
                b.setIcon(icons.icon(icon, "ink"))
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                b.setAutoRaise(True)
                b.setFocusPolicy(Qt.StrongFocus)
                b.clicked.connect(lambda _=False, f=fn: f())
                r.addWidget(b)
                self.buttons[key] = b
            r.addStretch(1)
            col_all.addLayout(r)
        self.status = _label("", "caption")                  # what is being fetched, or why not
        col_all.addWidget(self.status)
        col_all.addStretch(1)
        scroll = QScrollArea()                           # (a short window scrolls it, never clips it)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        self.album = None
        self.loaded = None

    def retint(self):
        self.empty.retint()
        for key, icon in (("find", "search"), ("file", "image"), ("url", "globe"), ("clear", "sparkles")):
            self.buttons[key].setIcon(icons.icon(icon, "ink"))

    def editable(self) -> bool:
        return not self.owner.busy() and bool((self.album or {}).get("files")) and \
            (self.album or {}).get("status") in planmod.EDITABLE

    def show_album(self, album):
        self.album = album
        self.loaded = None
        art = (album or {}).get("artwork") or {}
        self.stack.setCurrentIndex(1 if art.get("front") or self.editable() else 0)
        for pic, info in self.sides.values():
            pic.clear()
            info.setText("")
        on = self.editable()
        for b in self.buttons.values():
            b.setEnabled(on)
        self.buttons["clear"].setVisible(art.get("by") == planmod.BY_YOU)
        self.hint.setVisible(on)
        if not getattr(self.owner, "fetching", False):
            self.status.setText("")

    @staticmethod
    def original(folder):
        """The album's own cover file (called on the worker: a folder on a share can be slow)."""
        folder = Path(folder or "")
        try:
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and f.stem.lower() in COVER_NAMES:
                    return f
        except OSError:
            pass
        return None

    def load(self):
        """Read both images on a worker (only once the tab is shown)."""
        art = (self.album or {}).get("artwork") or {}
        if not self.album or self.loaded is self.album or not (art.get("front") or self.editable()):
            return
        self.loaded = album = self.album
        folder = album.get("source_dir") or ""
        front = Path(art["front"]) if art.get("front") else None
        first = next((fe["src"] for fe in album.get("files") or []), None)

        def work(_w):
            out = {}
            own = ArtworkView.original(folder)
            for k, p in (("old", own), ("new", front)):
                img = QImage(str(p)) if p else QImage()
                size = p.stat().st_size if p and p.exists() else 0
                where = p.name if p else ""
                if k == "old" and img.isNull() and first:     # (no cover file: the one in the tracks)
                    try:
                        import artwork
                        data = artwork.embedded_front(first)
                    except Exception:
                        data = None
                    if data:
                        img = QImage.fromData(data)
                        size, where = len(data), tr("Embedded in the tracks")
                out[k] = (img.scaled(224, 224, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not img.isNull()
                          else img, img.width(), img.height(), size, where)
            return out

        def done(out):
            if self.album is not album:
                return
            for k, (img, w, h, size, where) in out.items():
                pic, info = self.sides[k]
                if img.isNull():
                    pic.setText(tr("No cover"))
                    info.setText(tr("The album has no cover.") if k == "old" else
                                 tr("No new cover: the album keeps its own.") if not art.get("front") else "")
                    continue
                px = QPixmap.fromImage(img)
                px.setDevicePixelRatio(2.0)
                pic.setPixmap(px)
                src = ((tr("Your image: ") if art.get("by") == planmod.BY_YOU else "") + str(art.get("source", ""))) \
                    if k == "new" else where
                info.setText(f"{w} × {h} px · {size // 1024} KB" + (f"\n{src}" if src else ""))
        start(self.owner, work, done, failed=lambda _m: None)

    # ---- your cover -----------------------------------------------------
    def find_covers(self):
        """Every cover there is for the album, to pick one by eye (gui/cover_chooser.py)."""
        if not self.editable():
            return
        from .cover_chooser import open_chooser
        open_chooser(self.owner)

    def choose_file(self):
        if not self.editable():
            return
        dlg = QFileDialog(self.window(), tr("Choose an Image for the Cover"), str(self.album.get("source_dir") or ""))
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setFileMode(QFileDialog.ExistingFile)
        dlg.setNameFilter(tr("Images (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff)"))
        dlg.setLabelText(QFileDialog.Accept, tr("Use Image"))
        if not sheets.blocking():
            sheets.shown.append(("choose_file", tr("Choose an Image for the Cover"), ""))
            dlg.deleteLater()
            return
        ok = dlg.exec() == QDialog.Accepted and dlg.selectedFiles()
        path = dlg.selectedFiles()[0] if ok else ""
        dlg.deleteLater()
        if path:
            self.owner.cover_from_file(path)

    def ask_url(self):
        if not self.editable():
            return
        text, ok = sheets.get_text(self, tr("Use an image from the web as the cover"),
                                   tr("Paste the address of the image (a JPEG or PNG link). It is downloaded "
                                      "and checked; nothing is written until you apply the preview."),
                                   action=tr("Use Image"), placeholder="https://…/cover.jpg")
        if ok and text.strip():
            self.owner.cover_from_url(text.strip())

    def clear(self):
        self.owner.clear_cover()

    def _dropped(self, mime):
        """(a local image file, an http(s) link) from a drag."""
        for u in mime.urls() if mime.hasUrls() else []:
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES:
                return u.toLocalFile(), ""
            if u.scheme() in ("http", "https"):
                return "", u.toString()
        if mime.hasText():
            return "", image_link(mime.text())
        return "", ""

    def dragEnterEvent(self, e):
        if self.editable() and any(self._dropped(e.mimeData())):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        path, url = self._dropped(e.mimeData())
        if not self.editable() or not (path or url):
            return e.ignore()
        e.acceptProposedAction()
        if path:
            self.owner.cover_from_file(path)
        else:
            self.owner.cover_from_url(url)


class EvidenceView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(PAD, PAD, PAD, PAD)
        lay.setSpacing(0)
        self.fields, self.heads = {}, {}
        for i, key in enumerate(SECTIONS):
            if i:
                lay.addSpacing(16)                           # (16 between sections, 4 under a heading)
            sec = QVBoxLayout()
            sec.setSpacing(4)
            h = _label(tr(key), "headline", select=False)
            v = _label("", links=key == "Source")
            v.setOpenExternalLinks(True)
            v.setAccessibleName(tr(key))                      # (then "Key: its text": say())
            sec.addWidget(h)
            sec.addWidget(v)
            lay.addLayout(sec)
            self.fields[key], self.heads[key] = v, h
        lay.addSpacing(16)
        self.more = QToolButton()
        self.more.setProperty("role", "tool")
        self.more.setText(tr("Full preview text"))
        self.more.setCheckable(True)
        self.more.setArrowType(Qt.RightArrow)
        self.more.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more.setAccessibleName(tr("Show the full preview text"))
        self.more.toggled.connect(self.fold)
        lay.addWidget(self.more)
        self.full = QPlainTextEdit()
        self.full.setReadOnly(True)
        self.full.setAccessibleName(tr("What will change for the selected album"))
        self.full.setFont(style.mono())
        self.full.setMinimumHeight(220)
        self.full.hide()
        lay.addWidget(self.full)
        lay.addStretch(1)

    def fold(self, on):
        self.more.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.full.setVisible(on)

    def set(self, key, text):
        lb = self.fields[key]
        say(lb, tr(key), text or "—")
        self.heads[key].setVisible(True)

    def show_album(self, a, plan):
        ident = a.get("identification") or {}
        conf, margin = ident.get("confidence"), ident.get("margin")
        word = score_word(conf, plan)
        ahead = round(margin) if margin is not None else None
        self.set("Score", (f"{conf:.0f}" if conf is not None else tr("No score")) + (f" — {word}" if word else "")
                 + (tr(" ({0} ahead of the next release)").format(plural(ahead, tr('point'),
                                                                         tr('points'))) if ahead and conf is not None
                    else ""))
        prov = (ident.get("provider") or "").replace("musicbrainz", "MusicBrainz").replace("discogs", "Discogs")
        url = ident.get("url") or ""
        self.set("Source",
                 (prov or tr("None")) + (tr(' · <a href="{url}">open the release page</a>')
                                         .format(url=url) if url else ""))
        self.fields["Source"].setTextFormat(Qt.RichText if url else Qt.PlainText)
        self.set("What matched", "\n".join(f"• {x}" for x in (ident.get("evidence") or [])))
        doubts = []
        codes, words = reasons.parse(ident.get("reason", "") or "")
        codes = list(dict.fromkeys(list(codes) + list(ident.get("reason_codes") or [])))
        if ident.get("status") == "unresolved" or ident.get("tier") in ("partial", "album_only"):
            doubts += [reasons.describe(c).capitalize() + "." for c in codes]
            if words and not codes:
                doubts.append(words)
        doubts += list(ident.get("why") or [])
        doubts += [tr("Changed since preview: {c}").format(c=c) for c in a.get("conflicts") or []]
        doubts += list(a.get("notes") or [])
        doubts += [f"{s.get('label', 'Skipped')}: {Path(s['file']).name}" for s in a.get("skipped") or []
                   if s.get("file")]
        # (a key missing: the Setup notice says which, what it is for and where to set it)
        from . import setup
        errs = ident.get("provider_errors") or {}
        said = setup.error_notices(errs)
        doubts += said
        doubts += [tr("{k} had a problem: {v}").format(k=k, v=v) for k, v in errs.items()
                   if not (said and v == "missing_credentials" and setup.error_notices({k: v}))]
        if a.get("status") == "excluded" and a.get("rule"):
            doubts.insert(0, tr("{0} by the rule “{1}”.").format(a.get('label') or tr('Ignored'),
                                                                 a['rule'].get('name', '')))
        self.set("Doubts", "\n".join(f"• {x}" for x in doubts) or tr("None"))
        pv = ident.get("provenance") or {}
        by = {}
        for fld, src in pv.items():
            by.setdefault(str(src), []).append(fld)
        self.set("Where each tag came from", "\n".join(f"{s}: {', '.join(sorted(tag_name(x) for x in f)[:8])}"   # (Picard's names)
                                      + (" …" if len(f) > 8 else "") for s, f in sorted(by.items())))
        rel = a.get("release") or {}
        mbid = (rel.get("musicbrainz_albumid") or [""])[0] if isinstance(rel, dict) else ""
        self.set("Codes", "\n".join(filter(None, [
            tr("Release ID: {0}").format(ident['release_id']) if ident.get("release_id") else "",
            tr("MusicBrainz release: {mbid}").format(mbid=mbid) if mbid and mbid != ident.get("release_id") else "",
            (tr("Reason codes: ") + ", ".join(codes)) if codes else ""])) or tr("None"))

    def show_row(self, row):
        self.set("Score", tr("Not identified: marked Unresolved by an earlier run"))
        self.set("Source", "")
        self.set("What matched", "")
        reason = (row.get("reason") or "").strip()
        keys = []
        if "missing_credentials" in (row.get("codes") or []):   # (which key is missing now, and where to set it)
            from . import setup
            keys = [f"• {t}" for t in setup.key_notices()]
        self.set("Doubts", "\n".join([f"• {reasons.describe(c).capitalize()}." for c in row.get("codes") or []]
                                     + keys + ([f"• {reason[:1].upper()}{reason[1:]}"] if reason else [])))
        self.set("Where each tag came from", "")
        self.set("Codes", tr("Reason codes: ") + (", ".join(row.get("codes") or []) or "other"))


def where_files_go(album) -> str:
    """One line under the status: the folder the files go to, and how many are renamed."""
    files = [fe for fe in (album or {}).get("files") or [] if fe.get("dst") and fe.get("op") != "none"]
    if not files:
        return ""
    moved = {str(Path(fe["dst"]).parent) for fe in files if Path(fe["dst"]).parent != Path(fe["src"]).parent}
    renamed = sum(1 for fe in files if Path(fe["dst"]).name != Path(fe["src"]).name)
    parts = []
    if moved:
        to = sorted(moved)[0]
        parts.append(tr("Goes to {to}").format(to=to)
                     + (tr(" (and {0} more)").format(len(moved) - 1) if len(moved) > 1 else ""))
    if renamed:
        parts.append(tr("{0} renamed").format(plural(renamed, tr('file'), tr('files'))))
    return " · ".join(parts) or tr("Stays in its folder, names unchanged")


class Inspector(QDockWidget):
    edited = Signal(object)                              # an album of the plan, your edits applied

    def __init__(self, win):
        super().__init__(tr("Inspector"), win)
        self.win = win
        self.setObjectName("inspector")
        self.setTitleBarWidget(QWidget())                    # (no Qt title strip: the toolbar toggles it)
        self.setFeatures(QDockWidget.DockWidgetClosable)
        self.setAllowedAreas(Qt.RightDockWidgetArea)
        self.setAccessibleName(tr("Inspector"))
        w, lo, hi = style.INSPECTOR
        self.setMinimumWidth(lo)
        self.setMaximumWidth(hi)
        body = QWidget()
        body.setObjectName("inspectorBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.pages = QStackedWidget()
        self.empty = EmptyState("info", tr("Select an album to see its tags, artwork and evidence."))
        self.pages.addWidget(self.empty)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(PAD, 12, PAD, 8)
        hl.setSpacing(8)
        self.name = _label("", "headline")                  # (named with its text: say())
        hl.addWidget(self.name)
        st = QHBoxLayout()
        self.st_icon = QLabel()
        self.st_word = _label("", wrap=True)
        st.addWidget(self.st_icon)
        st.addWidget(self.st_word, 1)
        hl.addLayout(st)
        self.dest = _label("", "caption")                    # where the files go, at a glance
        hl.addWidget(self.dest)
        btns = QHBoxLayout()
        self.buttons = {}
        for key, text, icon in (("open", tr("Open Folder"), "folder-open"), ("web", tr("Search the Web"), "globe"),
                                ("release", tr("Change Release"), "disc-3")):
            b = QToolButton()
            b.setProperty("role", "tool")
            b.setText(text)
            # (Change Release, the fix for a wrong match, in words: a disc says too little; the window
            # gives it the Album > Change Release menu, opened by the whole button, its arrow inside)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if key == "release" else Qt.ToolButtonIconOnly)
            b.setIcon(icons.icon(icon, "ink"))
            b.setToolTip(text)
            b.setAccessibleName(text.rstrip("…"))
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.StrongFocus)
            btns.addWidget(b)
            self.buttons[key] = b
        btns.addStretch(1)
        hl.addLayout(btns)
        ml.addWidget(head)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName(tr("Inspector tabs"))
        self.tags = TagsView(self)
        self.art = ArtworkView(self)
        self.evidence = EvidenceView()
        for view, name, icon in ((self.tags, tr("Tags"), "tag"), (self.art, tr("Artwork"), "image"),
                                 (self.evidence, tr("Evidence"), "info")):
            self.tabs.addTab(view, name)
        self.tabs.currentChanged.connect(self.tab_changed)
        ml.addWidget(self.tabs, 1)
        self.pages.addWidget(main)
        lay.addWidget(self.pages)
        self.setWidget(body)
        self.full = self.evidence.full                     # (Overview's detail)
        self.album = None
        self.plan = None
        self.effect = None
        self.edits = {}                                  # your edits: {album folder: [edit]} (plan.apply_edits)
        self.history = []                                # self.edits before each of your edits (Undo Edit)
        self._wanted = {}                                # id(album) -> the latest edit asked for it
        self.fetching = False

    def retint(self):
        self.empty.retint()
        self.art.retint()
        self.tags.retint()
        for key, icon in (("open", "folder-open"), ("web", "globe"), ("release", "disc-3")):
            self.buttons[key].setIcon(icons.icon(icon, "ink"))
        if self.album:
            self._status(self.album)

    def tab_changed(self, i):
        if TABS[i] == "Artwork":
            self.art.load()
        from .common import _set_gui_pref
        _set_gui_pref("inspector_tab", TABS[i])

    def show_tab(self, name):
        if self.win.album_page():                           # (elsewhere it stays aside)
            self.setVisible(True)
        self.tabs.setCurrentIndex(TABS.index(name))

    def _status(self, a):
        key = a.get("status", "")
        self.st_icon.setPixmap(icons.icon(style.status_icon(key), key, 16).pixmap(16, 16))
        sym, word = label_for(a) if "identification" in a else ("!", tr("Unresolved"))
        say(self.st_word, tr("Status"), word)

    def show_album(self, album, plan=None):
        if album is None:
            return self.clear()
        same = album is self.album
        self.album = album
        self.plan = plan if plan is not None else (self.plan if same else None)
        self.pages.setCurrentIndex(1)
        say(self.name, tr("Album"), album_words(album["source_dir"]))
        self._status(album)
        say(self.dest, tr("Where the files go"), where_files_go(album))
        self.dest.setVisible(bool(self.dest.text()))
        self.buttons["release"].setEnabled(True)
        self.tags.show_album(album, keep=same)
        self.art.show_album(album)
        self.evidence.show_album(album, self.plan)
        self.tabs.setTabEnabled(0, True)
        self.tabs.setTabEnabled(1, True)
        if self.tabs.currentIndex() == 1 and self.isVisible():
            self.art.load()

    def show_row(self, row):
        """A Needs attention row (an album marked Unresolved by an earlier run)."""
        self.album = {"source_dir": row["folder"], "status": "unresolved"}
        self.pages.setCurrentIndex(1)
        say(self.name, tr("Album"), album_words(row["folder"]))
        self._status(self.album)
        self.dest.hide()
        say(self.st_word, tr("Status"),
            "Unresolved: " + ", ".join(reasons.describe(c) for c in row.get("codes") or ["other"]))
        self.buttons["release"].setEnabled(False)
        self.tags.show_album(None)
        self.art.show_album(None)
        self.evidence.show_row(row)
        self.tabs.setCurrentIndex(2)

    def clear(self):
        self.album = None
        self.pages.setCurrentIndex(0)
        self.full.clear()

    def toggle(self, on=None):
        on = not self.isVisible() if on is None else on
        if on and not self.isVisible() and not reduced_motion():
            self.effect = QGraphicsOpacityEffect(self.widget())
            self.widget().setGraphicsEffect(self.effect)
            a = QPropertyAnimation(self.effect, b"opacity", self)
            a.setDuration(180)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.finished.connect(lambda: self.widget().setGraphicsEffect(None))
            a.start()
            self._anim = a
        self.setVisible(on)

    # ---- your edits (entry points for the window) -------------------------------------------
    def busy(self) -> bool:
        """A preview or Apply is running (Apply reads the plan on a worker): no edit until it ends."""
        return bool(getattr((getattr(self.win, "pages", None) or {}).get("Overview"), "running", False))

    def can_edit(self, album) -> bool:
        return bool(album) and album.get("status") in planmod.EDITABLE and not self.busy()

    def pending(self) -> bool:
        """An edit or a cover is still on its way to the preview (Apply waits for it)."""
        return bool(self._wanted) or bool(self.fetching)

    def edits_for_preview(self) -> dict:
        """Your edits, for engine.Engine.edits before a new preview (a copy: the worker reads it)."""
        return copy.deepcopy(self.edits)

    def forget_edits(self):
        """Drop every edit (a new folder is previewed)."""
        self.edits = {}
        self.history = []
        self._wanted = {}
        self._actions_changed()

    HISTORY = 50                                         # the edits Undo Edit can take back, newest last

    def remember(self):
        """Keep your edits as they are now, for Edit > Undo Edit (call it before an edit)."""
        self.history.append(copy.deepcopy(self.edits))
        del self.history[:-self.HISTORY]
        self._actions_changed()

    def can_undo_edit(self) -> bool:
        return bool(self.history) and not self.busy()

    def undo_edit(self) -> int:
        """Your last edit of the preview taken back (tags, cover or tags from file names): the
        edits as they were before it, and each album it touched planned again from them (on a
        worker, as any edit). Returns the number of albums it touched (0: nothing to undo)."""
        if not self.can_undo_edit():
            return 0
        old = self.history.pop()
        changed = {k for k in set(old) | set(self.edits) if old.get(k) != self.edits.get(k)}
        self.edits = old
        ov = (getattr(self.win, "pages", None) or {}).get("Overview")
        plan = getattr(ov, "plan", None)
        n = 0
        for a in (plan or {}).get("albums") or []:
            if os.path.realpath(a["source_dir"]) in changed:
                n += 1
                if plan is self.plan:                    # (another plan: the next preview has them)
                    self.reapply(a)
        self._actions_changed()
        return n or len(changed)

    def _actions_changed(self):
        win = self.win
        if hasattr(win, "update_tag_actions") and hasattr(win, "commands"):
            win.update_tag_actions()

    def edit_of(self, album, create=False):
        """The edit of album in self.edits (made when create), or None."""
        key = os.path.realpath(album["source_dir"])
        files = [fe["src"] for fe in album.get("files") or []]
        e = planmod.edit_for(self.edits, key, files)
        if e is None and create:
            e = {"files": files, "tags": {}, "file_tags": {}, "cover": None}
            self.edits.setdefault(key, []).append(e)
        return e

    def _drop_if_empty(self, album):
        e = self.edit_of(album)
        if e is not None and planmod.edit_empty(e):
            key = os.path.realpath(album["source_dir"])
            self.edits[key] = [x for x in self.edits.get(key) or [] if x is not e]
            if not self.edits[key]:
                del self.edits[key]

    def set_tag(self, field_, value, file=None, album=None):
        """Your value for a tag of the album shown: a list of values, None (the tag removed) or
        plan.KEEP (the file's own value: Revert to Original); file: for that file only, else
        for every file of the album (and in place of each file's own edit of that tag)."""
        album = album or self.album
        if not self.can_edit(album):
            return
        self.remember()
        e = self.edit_of(album, create=True)
        if file:
            e["file_tags"].setdefault(file, {})[field_] = value
        else:
            e["tags"][field_] = value
            for ft in e["file_tags"].values():
                ft.pop(field_, None)
        self.reapply(album)

    def clear_tag(self, field_, file=None, album=None):
        """Your edit of a tag dropped: its new value is the one the preview suggested again."""
        album = album or self.album
        e = self.edit_of(album) if self.can_edit(album) else None
        if e is None:
            return
        self.remember()
        if file:
            (e["file_tags"].get(file) or {}).pop(field_, None)
        else:
            e["tags"].pop(field_, None)
            for ft in e["file_tags"].values():
                ft.pop(field_, None)
        e["file_tags"] = {k: v for k, v in e["file_tags"].items() if v}
        self._drop_if_empty(album)
        self.reapply(album)

    def set_cover(self, cover: dict, album=None):
        """Your image (stage_cover's dict) as the album's new cover."""
        album = album or self.album
        if not self.can_edit(album):
            return
        self.remember()
        self.edit_of(album, create=True)["cover"] = cover
        self.reapply(album)

    def clear_cover(self, album=None):
        album = album or self.album
        e = self.edit_of(album) if self.can_edit(album) else None
        if e is None or not e.get("cover"):
            return
        self.remember()
        e["cover"] = None
        self._drop_if_empty(album)
        self.reapply(album)

    def reapply(self, album):
        """The album's entry of the plan again from what was planned, with your edits (on a
        worker: it reads the files' tags); then the tabs and edited(album)."""
        edit = copy.deepcopy(self.edit_of(album))
        snap = copy.deepcopy(album)
        plan = self.plan                                 # (the plan the album is of)
        prof_data = (plan or {}).get("profile_data")
        token = object()
        self._wanted[id(album)] = token
        self.tags.note.setText(tr("Applying your edit to the preview…"))

        def work(_w):
            prof = settings = None
            if prof_data:
                try:
                    import profiles as pf
                    prof = pf.from_dict(prof_data, check=False)
                    settings = planmod.file_settings(prof)
                except Exception:
                    prof = settings = None
            return planmod.apply_edits(snap, edit, prof, settings)

        def done(new):
            if self._wanted.get(id(album)) is not token:
                return                                   # (a later edit of it is on its way)
            self._wanted.pop(id(album), None)
            ov = (getattr(self.win, "pages", None) or {}).get("Overview")
            if self.busy() or album.get("status") not in planmod.EDITABLE or \
                    (plan is not None and ov is not None and plan is not getattr(ov, "plan", None)) or \
                    (plan is not None and not any(a is album for a in plan.get("albums") or [])):
                if self.album is album:                  # (applied, or a new preview: this plan is gone)
                    self.tags.fill()
                return
            album.clear()
            album.update(new)
            if plan is not None:
                planmod.recount(plan)
            if self.album is album:
                self._status(album)
                self.tags.show_album(album, keep=True)
                self.art.show_album(album)
                if self.tabs.currentIndex() == TABS.index("Artwork"):
                    self.art.load()
            self.edited.emit(album)
            if album.get("renames") and ov is not None and plan is getattr(ov, "plan", None):
                # (an edit of a field the naming reads - a disc number: where the files go changes;
                # the album is previewed again with your edits, and Apply waits for it)
                self.tags.note.setText(tr("Your edit changes where the files go: this album is previewed "
                                          "again with it."))
                ov.look_up(ov.sources.currentData() or "profile", [album], again=True, queued=True)

        def failed(msg):
            if self._wanted.get(id(album)) is not token:
                return                                   # (a later edit of it is on its way)
            self._wanted.pop(id(album), None)
            self.tags.fill()
            sheets.warning(self, tr("Your edit could not be applied to the preview."), msg.split("\n\n")[0])
        start(self, work, done, failed=failed)

    def _fetch(self, what, get, source):
        album = self.album
        if not self.can_edit(album):
            return
        plan = self.plan                                 # (the plan the album is of)
        self.fetching = True
        say(self.art.status, tr("Cover"), tr("Reading {what}…").format(what=what))

        def work(_w):
            return stage_cover(get(), source)

        def done(cover):
            self.fetching = False
            say(self.art.status, tr("Cover"), "")
            ov = (getattr(self.win, "pages", None) or {}).get("Overview")
            gone = plan is not self.plan or not any(a is album for a in (plan or {}).get("albums") or []) or \
                (ov is not None and plan is not getattr(ov, "plan", None))
            if self.busy() or gone:                      # (Apply runs, or a new preview: this plan is gone)
                self.win.statusBar().showMessage(tr("The cover was not used: the preview changed while it "
                                                    "was being read."), 6000)
                return
            self.set_cover(cover, album)

        def failed(msg):
            self.fetching = False
            why = msg.split("\n\n")[0]
            say(self.art.status, tr("Cover"), why)
            sheets.warning(self, tr("This image cannot be used as the cover."), why)
        start(self, work, done, failed=failed)

    def cover_from_file(self, path):
        """An image file as the album's new cover (read and checked on a worker)."""
        import artwork
        p = Path(path)

        def get():
            if p.stat().st_size > artwork.MAX_BYTES:
                raise ValueError(tr("The image is larger than {0} MB.").format(artwork.MAX_BYTES // 1_000_000))
            return p.read_bytes()
        self._fetch(p.name, get, p.name)

    def cover_from_url(self, url):
        """An image link as the album's new cover (downloaded with artwork's HTTP helper on a
        worker, at most artwork.MAX_BYTES, and checked)."""
        link = image_link(url)
        if not link:
            sheets.warning(self, tr("That is not an image link."),
                           tr("Paste an address that starts with https:// or http://."))
            return
        import artwork

        def get():
            try:
                return artwork._get(link, limit=artwork.MAX_BYTES)
            except ValueError:
                raise ValueError(tr("The image is larger than {0} MB.").format(artwork.MAX_BYTES // 1_000_000))
            except OSError as exc:
                raise ValueError(tr("It could not be downloaded: {0}.").format(getattr(exc, 'reason', exc)))
        self._fetch(tr("the image link"), get, QUrl(link).host() or link)
