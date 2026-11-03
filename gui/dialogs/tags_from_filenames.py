"""gui/dialogs/tags_from_filenames.py - Tags from File Names (Picard's "Convert File Names to Tags").

A pattern such as "%artist% - %album%/%tracknumber% %title%" is matched against the end of each
file's path ("/" separates folders); a preview shows what each file name gives, and "Use These
Tags" hands the tags on as plan overrides: {file path: {field: [values]}}. Nothing is written to a
file here - the tags reach the files only when the plan is applied (staged, verified, journaled
and undoable as every write).

TagMatchExpression is Picard's (picard/ui/tagsfromfilenames.py, GPL-2.0-or-later).

The window calls show_tags_from_filenames(parent, files, on_apply): on_apply(overrides) runs when
the person chooses Use These Tags. apply_in_background(owner, plan, overrides, done, store=
inspector.edits, reapply=inspector.reapply) puts them into a plan on screen as your own edits
(plan.apply_edits, the inspector's hook), in the app's store of edits {realpath(album folder):
[edit, ...]} so the inspector and a new preview keep them; apply_to_plan does the same at once for
a plan not on screen."""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import sheets, style
from ..common import _gui_prefs, _set_gui_pref, heading, left_header, plural
from ..i18n import N_, tr
from ..tagnames import display

# Picard's presets, in Picard's order
PRESETS = (
    "%artist%/%album%/%title%",
    "%artist%/%album%/%tracknumber% %title%",
    "%artist%/%album%/%tracknumber% - %title%",
    "%artist%/%album% - %tracknumber% - %title%",
    "%artist% - %album%/%title%",
    "%artist% - %album%/%tracknumber% %title%",
    "%artist% - %album%/%tracknumber% - %title%",
)
PREF = "tags_from_filenames_format"
PREF_UNDERSCORES = "tags_from_filenames_underscores"


# ---- Picard's matcher -----------------------------------------------------
def _normalize(name):
    return "~" + name[1:] if name.startswith("_") else name


FIELDS_WORDS = N_("Fields: %artist% %album% %tracknumber% %title% %date% … and a / between folders")
REPLACE_WORDS = (N_("These values replace the preview's values of the same tags, and are written when you apply. "
                    "Edit > Undo Edit (Ctrl+Z) takes them back before then."))

class TagMatchExpression:
    _numeric_tags = ("tracknumber", "totaltracks", "discnumber", "totaldiscs")

    def __init__(self, expression, replace_underscores=False):
        self.replace_underscores = replace_underscores
        self._tag_re = re.compile(r"(%\w+%)")
        self._parse(expression)

    def _parse(self, expression):
        self._group_map = OrderedDict()
        format_re = ["(?:^|/)"]
        for i, part in enumerate(self._tag_re.split(expression)):
            if part.startswith("%") and part.endswith("%") and len(part) > 2:
                name = part[1:-1]
                group = "%s_%i" % (name, i)
                tag = _normalize(name)
                self._group_map[group] = tag
                if tag in self._numeric_tags:
                    format_re.append(r"(?P<" + group + r">\d+)")
                elif tag == "date":
                    format_re.append(r"(?P<" + group + r">\d+(?:-\d+(?:-\d+)?)?)")
                else:
                    format_re.append(r"(?P<" + group + r">[^/]*?)")
            else:
                format_re.append(re.escape(part))
        format_re.append(r"(?:\.\w+)?$")                  # (the extension, if any)
        self._format_re = re.compile("".join(format_re))

    @property
    def matched_tags(self):
        return list(OrderedDict.fromkeys(self._group_map.values()))

    def match_file(self, filename):
        m = self._format_re.search(str(filename).replace("\\", "/"))
        if not m:
            return {}
        result = {}
        for group, tag in self._group_map.items():
            value = m.group(group).strip()
            if tag in self._numeric_tags:
                value = value.lstrip("0")
            if self.replace_underscores:
                value = value.replace("_", " ")
            result.setdefault(tag, []).append(value)
        return result


def parse(files, pattern, replace_underscores=False) -> dict:
    """{path: {field: [values]}} for every file the pattern matches (a field it gives empty -
    "01 .flac" for "%tracknumber% %title%" - is left out; so is a hidden ~variable)."""
    expr = TagMatchExpression(pattern, replace_underscores)
    out = {}
    for f in files:
        tags = {k: [v for v in vals if v] for k, vals in expr.match_file(f).items() if not k.startswith("~")}
        tags = {k: v for k, v in tags.items() if v}
        if tags:
            out[str(f)] = tags
    return out


def covered(path, pattern) -> str:
    """The end of a file's path that the pattern is matched against: one part for each "/" in the
    pattern, and the file's name ("Aurora Vale/Northern Lights/01 Track 1.flac")."""
    parts = Path(str(path)).parts
    n = (pattern or "").count("/") + 1
    return "/".join(parts[-n:]) if parts else str(path)


# ---- into a plan ------------------------------------------------------------
def plan_files(plan_or_album) -> list:
    """The audio files of an album entry (or of every album of a plan), as the dialog takes them."""
    albums = plan_or_album.get("albums") if "albums" in plan_or_album else [plan_or_album]
    return [fe["src"] for a in albums or [] for fe in a.get("files") or []]


def merged_edit(entry, overrides, edit=None) -> dict | None:
    """The album's edit (plan.py's: {"files", "tags", "file_tags", "cover"}: `edit`, else the one
    the album was last made with) with the tags from the file names added for its files: what you
    edited before stays, a file name's value wins over an earlier one for the same field of the
    same file. A new dict (edit is left as it is); None when no file of the album is in overrides."""
    mine = {fe["src"] for fe in entry.get("files") or []}
    got = {f: dict(tags) for f, tags in overrides.items() if f in mine}
    if not got:
        return None
    old = edit if edit is not None else (entry.get("edited") or {})
    # (the keys the store already has stay as they are - the inspector looks a file up by its
    # plan src - and a new file is keyed by its src: one entry per file, even through a symlink)
    file_tags = {k: {f: list(v) for f, v in tags.items()} for k, tags in (old.get("file_tags") or {}).items()}
    real = {os.path.realpath(k): k for k in file_tags}
    for f, tags in got.items():
        k = f if f in file_tags else real.get(os.path.realpath(f), f)
        real.setdefault(os.path.realpath(f), k)
        file_tags.setdefault(k, {}).update({t: list(v) for t, v in tags.items()})
    files = sorted(set(old.get("files") or []) | mine)
    return {"files": files, "tags": dict(old.get("tags") or {}), "file_tags": file_tags,
            "cover": old.get("cover")}


def merge_into_store(plan, overrides, store=None) -> tuple:
    """The file names' tags put into the app's store of edits, {realpath(album folder): [edit,
    ...]} (Inspector.edits, engine.Engine.edits; a new one when store is None), album by album:
    the album's own edit there (plan.edit_for) is updated in place, so the inspector keeps them in
    its next edit; one is added only for an album that has none. Dict work only (no file is read):
    run it on the UI thread. Returns ([(album, its edit)], [(path, why left out)], store)."""
    from ..common import planmod
    store = {} if store is None else store
    left = dict.fromkeys(overrides)
    targets, out = [], []
    for a in plan.get("albums") or []:
        mine = [fe["src"] for fe in a.get("files") or []]
        if not any(f in overrides for f in mine):
            continue
        for f in mine:
            left.pop(f, None)
        if a.get("status") not in planmod.EDITABLE:
            out += [(f, tr("its album is not to be written (blocked, excluded or "
                           "done)")) for f in mine if f in overrides]
            continue
        key = os.path.realpath(a["source_dir"])
        have = planmod.edit_for(store, key, mine)
        edit = merged_edit(a, overrides, have if have is not None else a.get("edited"))
        if have is not None:
            have.clear()
            have.update(edit)                            # (the same dict: the inspector's edit_of finds it)
            edit = have
        else:
            got = store.get(key)
            if isinstance(got, dict):                     # (an older store held one edit per folder)
                store[key] = got = [got]
            store.setdefault(key, []).append(edit)
        targets.append((a, edit))
    out += [(p, tr("not in the plan (preview its folder first)")) for p in left]
    return targets, out, store


def _profile(plan, profile=None):
    from ..common import pf, planmod
    if profile is None:
        try:
            profile = pf.from_dict(plan.get("profile_data") or {}, check=False)
        except Exception:
            profile = None
    try:
        settings = planmod.file_settings(profile) if profile is not None else None
    except Exception:
        settings = None
    return profile, settings


def apply_to_plan(plan, overrides, store=None, profile=None) -> tuple:
    """Put {path: {field: [values]}} into a plan made earlier, through plan.apply_edits (the
    inspector's own edits: each change shown as yours, kept by a new preview once the store is
    handed to the engine). store: the app's edits, {realpath(album folder): [edit, ...]}, updated
    as merge_into_store says. Returns (albums changed, [(path, why left out)], store). It changes
    the plan in place and reads the files' tags: for a plan on screen use apply_in_background,
    which works on copies and swaps them in on the UI thread."""
    from ..common import planmod
    targets, out, store = merge_into_store(plan, overrides, store)
    profile, settings = _profile(plan, profile)
    for a, edit in targets:
        planmod.apply_edits(a, edit, profile, settings)
    planmod.recount(plan)
    return len(targets), out, store


_WANTED = {}                                              # id(album) -> the latest file-name tags asked for it


def apply_in_background(owner, plan, overrides, done, store=None, reapply=None, busy=None):
    """The file names' tags into a plan on screen. The store of edits is updated now, on the UI
    thread (merge_into_store); then
    - reapply given (the inspector's Inspector.reapply, for its own plan): reapply(album) for each
      album, which copies, works on a worker and swaps in, dropping any earlier edit of it still
      on its way; done(plan, result) at once;
    - else: plan.apply_edits on deep copies of the albums on a worker, and in done, on the UI
      thread, each copy swapped into its album (a result overtaken by a later one is dropped),
      the summary counted again, then done(plan, result). No copy is swapped in while a preview
      or an Apply runs (busy(), or the window's Overview running: Apply reads the plan on a
      worker), once the plan is no longer the Overview's, or for an album no longer to be
      written; the store holds the edit all the same, so the next preview has it.
    result: (albums changed, [(path, why left out)], store)."""
    import copy
    from ..common import planmod
    from ..work import start
    targets, out, store = merge_into_store(plan, overrides, store)
    result = (len(targets), out, store)
    if reapply is not None:
        for a, _e in targets:
            _WANTED.pop(id(a), None)                      # (an earlier result on its way is dropped)
            reapply(a)
        done(plan, result)
        return None
    token = object()
    for a, _e in targets:
        _WANTED[id(a)] = token
    jobs = [(a, copy.deepcopy(a), copy.deepcopy(e)) for a, e in targets]
    prof_data = copy.deepcopy(plan.get("profile_data"))

    def work(_w):
        profile, settings = _profile({"profile_data": prof_data})
        return [planmod.apply_edits(snap, edit, profile, settings) for _a, snap, edit in jobs]

    def finished(news):
        ov = (getattr(sheets._window(owner), "pages", None) or {}).get("Overview")
        held = bool(busy and busy()) or bool(getattr(ov, "running", False)) or \
            (ov is not None and plan is not getattr(ov, "plan", None))
        for (a, _snap, _e), new in zip(jobs, news):
            if _WANTED.get(id(a)) is not token:
                continue                                  # (a later one for this album is on its way)
            _WANTED.pop(id(a), None)
            if held or a.get("status") not in planmod.EDITABLE:
                continue                                  # (applying, or a new preview: the store has it)
            a.clear()
            a.update(new)
        planmod.recount(plan)
        done(plan, result)

    def failed(msg):
        for a, _snap, _e in jobs:
            if _WANTED.get(id(a)) is token:
                _WANTED.pop(id(a), None)
        sheets.warning(owner if isinstance(owner, QWidget) else None,
                        tr("The tags from the file names could not be put into the preview."),
                        (msg or "").split("\n\n")[0])
    return start(owner, work, finished, failed=failed)


# ---- the sheet ------------------------------------------------------------
class TagsFromFileNames(QDialog):
    """A pattern, the preview table (file -> the tags it gives) and Use These Tags."""

    chosen = Signal(dict)                               # {path: {field: [values]}}

    def __init__(self, parent, files, on_apply=None):
        super().__init__(sheets._window(parent))
        sheets.prepare(self, tr("Tags from File Names"))
        self.files = [str(f) for f in files]
        self.on_apply = on_apply
        self.result_tags = {}
        self.setMinimumSize(640, 420)
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        lay.addLayout(heading(tr("Tags from File Names"),
                              tr("Describe how your files are named. Nothing is written until you apply the preview.")))
        row = QHBoxLayout()
        lab = QLabel(tr("&Pattern:"))
        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.pattern.setInsertPolicy(QComboBox.NoInsert)
        self.pattern.setAccessibleName(tr("Pattern"))
        self.pattern.setFont(style.mono())
        self.pattern.lineEdit().setFont(style.mono())       # (the stylesheet would give it the UI font)
        self.pattern.setProperty("role", "mono")
        lab.setBuddy(self.pattern)
        prefs = _gui_prefs()
        items = list(PRESETS)
        last = prefs.get(PREF) or ""
        if last and last not in items:
            items.insert(0, last)
        self.pattern.addItems(items)
        self.pattern.setCurrentIndex(items.index(last) if last in items else 0)
        row.addWidget(lab)
        row.addWidget(self.pattern, 1)
        lay.addLayout(row)
        self.fields = QLabel(tr(FIELDS_WORDS))                 # (the reference, under the field it is for)
        self.fields.setProperty("role", "caption")
        self.fields.setWordWrap(True)
        self.fields.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.fields.setContentsMargins(lab.sizeHint().width() + row.spacing(), 0, 0, 0)
        lay.addWidget(self.fields)
        self.underscores = QCheckBox(tr("Replace &underscores with spaces"))
        self.underscores.setAccessibleName(tr("Replace underscores with spaces"))
        self.underscores.setChecked(bool(prefs.get(PREF_UNDERSCORES)))
        lay.addWidget(self.underscores)
        self.table = QTableWidget(0, 1)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setAccessibleName(tr("Preview: the tags each file name gives"))
        left_header(self.table)
        lay.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setProperty("role", "caption")
        lay.addWidget(self.summary)
        self.replaces = QLabel(tr(REPLACE_WORDS))               # (what Use These Tags does, and its undo)
        self.replaces.setProperty("role", "hint")
        self.replaces.setWordWrap(True)
        lay.addWidget(self.replaces)
        buttons, self.ok = sheets.button_row(self, tr("Use These Tags"), self.use)
        lay.addSpacing(style.SPACE["s"])
        lay.addLayout(buttons)
        self._timer = QTimer(self)                      # (typing: the preview follows a moment later)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.preview)
        self.pattern.editTextChanged.connect(lambda _t: self._timer.start())
        self.pattern.currentIndexChanged.connect(lambda _i: self.preview())
        self.underscores.toggled.connect(lambda _b: self.preview())
        self.preview()

    def text(self) -> str:
        return self.pattern.currentText().strip()

    def preview(self):
        expr = TagMatchExpression(self.text(), self.underscores.isChecked())
        cols = expr.matched_tags
        self.table.clear()
        self.table.setColumnCount(len(cols) + 1)
        self.table.setHorizontalHeaderLabels([tr("File")] + [display(c) for c in cols])
        self.table.setRowCount(len(self.files))
        self.result_tags = parse(self.files, self.text(), self.underscores.isChecked()) if cols else {}
        for i, f in enumerate(self.files):
            name = QTableWidgetItem(covered(f, self.text()))      # (the part of the path the pattern reads)
            name.setToolTip(f)
            name.setData(Qt.UserRole, f)
            self.table.setItem(i, 0, name)
            got = expr.match_file(f) if cols else {}
            for j, c in enumerate(cols):
                it = QTableWidgetItem("; ".join(got.get(c, [])) if got else "")
                self.table.setItem(i, j + 1, it)
            if not got and cols:
                miss = QTableWidgetItem(tr("Does not match the pattern"))
                miss.setForeground(style.status_color("excluded"))
                self.table.setItem(i, 1, miss)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(True)
        n = len(self.result_tags)
        if not cols:
            self.summary.setText(tr("The pattern names no field: put one between % signs, such as %title%."))
        elif n:
            self.summary.setText(tr("{0} of {1} match.").format(plural(n, tr('file'), tr('files')), len(self.files)))
        else:
            self.summary.setText(tr("No file name matches the pattern."))
        self.ok.setEnabled(bool(n))

    def use(self):
        self.preview()
        if not self.result_tags:
            return
        _set_gui_pref(PREF, self.text())
        _set_gui_pref(PREF_UNDERSCORES, self.underscores.isChecked())
        self.chosen.emit(self.result_tags)
        if self.on_apply:
            self.on_apply(self.result_tags)
        self.accept()


def show_tags_from_filenames(parent, files, on_apply=None):
    """Open the sheet over parent's window for these files; on_apply(overrides) when chosen.
    Offscreen (tests) the sheet is recorded and not shown; the dialog is returned either way."""
    dlg = TagsFromFileNames(parent, files, on_apply)
    if not files:
        sheets.information(parent, tr("Select an album first."),
                           tr("Tags from File Names reads the names of the selected album's files."))
        dlg.deleteLater()
        return None
    sheets.run(dlg, "tags_from_filenames")
    return dlg

