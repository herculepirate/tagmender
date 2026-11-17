"""gui/dialogs/tags_from_filenames.py - Tags from File and Folder Names (Picard's "Convert File
Names to Tags", read over folders too, as foobar2000's "Guess values from file names").

A pattern such as "%artist% - %album%/%tracknumber% %title%" is matched against each file's path
("/" separates folders): its end, or (Read from) the whole path below a folder you choose - the
album's folder or the one holding it - so "%albumartist%/%date% - %album% [%catalognumber%]/
%discnumber%-%tracknumber% - %title%" reads the artist folder, the album folder and the file name.
Patterns guessed from the files' own names are offered first. A preview shows what each file
gives, and "Use These Tags" hands the tags on as plan overrides: {file path: {field: [values]}}. Nothing is written to a
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
# patterns over a folder you choose (Read from: the album's folder, or the one holding it)
FOLDER_PRESETS = (
    "%tracknumber% - %title%",
    "%tracknumber% %title%",
    "%discnumber%-%tracknumber% - %title%",
    "CD%discnumber%/%tracknumber% - %title%",
    "%album%/%tracknumber% - %title%",
    "%date% - %album%/%tracknumber% - %title%",
    "%albumartist%/%date% - %album%/%tracknumber% - %title%",
    "%albumartist%/%date% - %album% [%catalognumber%]/%tracknumber% - %title%",
    "%albumartist%/%date% - %album% [%catalognumber%]/%discnumber%-%tracknumber% - %title%",
    "%albumartist% - %album%/%tracknumber% - %title%",
)
PREF_BASE = "tags_from_filenames_base"
BASES = (("album", N_("The album's folder")), ("parent", N_("The folder holding the album")),
         ("grandparent", N_("Two folders above the album (its artist's folder read too)")),
         ("end", N_("The end of each path")))
PREF = "tags_from_filenames_format"
PREF_UNDERSCORES = "tags_from_filenames_underscores"


# ---- Picard's matcher -----------------------------------------------------
def _normalize(name):
    return "~" + name[1:] if name.startswith("_") else name


FIELDS_WORDS = N_("Fields: %albumartist% %artist% %album% %date% %catalognumber% %discnumber% %tracknumber% "
                  "%title% … and a / between folders")
REPLACE_WORDS = (N_("These values replace the preview's values of the same tags, and are written when you apply "
                    "(an album not looked up: when you save its tags). Edit > Undo Edit (Ctrl+Z) takes them back "
                    "before then."))

class TagMatchExpression:
    _numeric_tags = ("tracknumber", "totaltracks", "discnumber", "totaldiscs")

    def __init__(self, expression, replace_underscores=False, anchored=False):
        self.replace_underscores = replace_underscores
        self.anchored = anchored                           # (the whole path below a folder, not its end)
        self._tag_re = re.compile(r"(%\w+%)")
        self._parse(expression)

    def _parse(self, expression):
        self._group_map = OrderedDict()
        format_re = ["^" if self.anchored else "(?:^|/)"]
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


def relative(path, base) -> str | None:
    """PATH below folder BASE, "/" between its folders ("Alpha/CD1/01 x.flac"); None when it is not
    below it."""
    try:
        rel = os.path.relpath(str(path), str(base))
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def read_part(path, base=None) -> str | None:
    """What a pattern is matched against: the path below BASE, else (None) the whole path's end."""
    return str(path) if base is None else relative(path, base)


def parse(files, pattern, replace_underscores=False, base=None) -> dict:
    """{path: {field: [values]}} for every file the pattern matches (a field it gives empty -
    "01 .flac" for "%tracknumber% %title%" - is left out; so is a hidden ~variable). base: the
    folder the pattern reads from (the whole path below it); None: the path's end."""
    expr = TagMatchExpression(pattern, replace_underscores, anchored=base is not None)
    out = {}
    for f in files:
        part = read_part(f, base)
        if part is None:
            continue
        tags = {k: [v for v in vals if v] for k, vals in expr.match_file(part).items() if not k.startswith("~")}
        tags = {k: v for k, v in tags.items() if v}
        if tags:
            out[str(f)] = tags
    return out


# a folder's or file's name read as fields (guess): the first that fits, most telling first
_DISC_DIR = re.compile(r"^(CD|Disc|Disk|DISC|disc|cd)(\s*)\d{1,2}$")
_NAME_RULES = (
    (re.compile(r"^\d{1,2}-\d{1,3} - .+$"), "%discnumber%-%tracknumber% - %title%"),
    (re.compile(r"^\d{1,3} - .+ - .+$"), "%tracknumber% - %artist% - %title%"),
    (re.compile(r"^\d{1,3} - .+$"), "%tracknumber% - %title%"),
    (re.compile(r"^\d{1,3}\. .+$"), "%tracknumber%. %title%"),
    (re.compile(r"^\d{1,3} .+$"), "%tracknumber% %title%"),
)
_ALBUM_RULES = (
    (re.compile(r"^\d{4}(?:-\d\d(?:-\d\d)?)? - .+ \[[^\]]+\]$"), "%date% - %album% [%catalognumber%]"),
    (re.compile(r"^\(\d{4}\) .+$"), "(%date%) %album%"),
    (re.compile(r"^\d{4}(?:-\d\d(?:-\d\d)?)? - .+$"), "%date% - %album%"),
    (re.compile(r"^.+ \(\d{4}\)$"), "%album% (%date%)"),
    (re.compile(r"^.+ - .+$"), "%albumartist% - %album%"),
)


def _guess_name(name, rules, fallback):
    for rx, pat in rules:
        if rx.match(name):
            return pat
    return fallback


def guess(files, base=None) -> list:
    """Patterns guessed from the files' own names (and their folders' names below BASE): each
    folder and file name read as what it most likely holds - "CD1" a disc, "1994 - Voyage [478 091-2]"
    a date, an album and a catalogue number, "02-05 - Song" a disc, track and title. Only those that
    fit every file (else the most), best first; [] when none fits any."""
    rels = [r for r in (read_part(f, base) for f in files) if r]
    if not rels:
        return []
    cands = []
    for rel in rels[:20]:
        parts = rel.split("/") if base is not None else rel.split("/")[-3:]
        stem = parts[-1].rsplit(".", 1)[0] if "." in parts[-1] else parts[-1]
        out = [_guess_name(stem, _NAME_RULES, "%title%")]
        dirs = parts[:-1]
        named = [d for d in dirs if not _DISC_DIR.match(d)]
        for d in reversed(dirs):
            m = _DISC_DIR.match(d)
            if m:
                out.insert(0, f"{m.group(1)}{m.group(2)}%discnumber%")
            elif d is named[-1]:
                out.insert(0, _guess_name(d, _ALBUM_RULES, "%album%"))
            else:
                out.insert(0, "%albumartist%")
        if base is None:                               # (the end of the path: the file and its album)
            out = out[-2:] if not any("%discnumber%" in x for x in out[:-1]) else out[-3:]
        if "%discnumber%" in out[-1]:                  # ("02-05 - Song": the disc is in the name)
            out = [x for x in out[:-1] if "%discnumber%" not in x] + out[-1:]
        cands.append("/".join(out))
    scored = []
    for c in dict.fromkeys(cands):
        n = len(parse(files, c, base=base))
        if n:
            scored.append((-n, cands.index(c), c))
    return [c for _n, _i, c in sorted(scored)]


def covered(path, pattern, base=None) -> str:
    """The part of a file's path that the pattern is matched against: below BASE; else one part
    for each "/" in the pattern, and the file's name ("Aurora Vale/Northern Lights/01 Track 1.flac")."""
    if base is not None:
        return relative(path, base) or str(path)
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
        planmod.plain_files(a)                           # (EDT-2: an album not looked up: its files)
        if not planmod.editable(a):
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
            if held or not planmod.editable(a):
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

    def __init__(self, parent, files, on_apply=None, folder=None):
        super().__init__(sheets._window(parent))
        sheets.prepare(self, tr("Tags from File and Folder Names"))
        self.files = [str(f) for f in files]
        # (Read from: the album's folder FOLDER, the one holding it, or - no folder - each path's end)
        self.bases = []
        if folder:
            up = os.path.dirname(str(folder).rstrip(os.sep)) or os.sep
            self.bases = [("album", str(folder)), ("parent", up), ("grandparent", os.path.dirname(up) or os.sep)]
        self.bases.append(("end", None))
        self.on_apply = on_apply
        self.result_tags = {}
        self.setMinimumSize(640, 420)
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"])
        lay.addLayout(heading(tr("Tags from File and Folder Names"),
                              tr("Describe how your files and their folders are named. Nothing is written until you "
                                 "apply the preview, or save the tags of an album not looked up.")))
        prefs = _gui_prefs()
        brow = QHBoxLayout()
        blab = QLabel(tr("&Read from:"))
        self.base = QComboBox()
        self.base.setAccessibleName(tr("Read from"))
        words = dict(BASES)
        for key, path in self.bases:
            self.base.addItem(tr(words[key]) + (f" ({Path(path).name or path})" if path else ""), key)
        want = prefs.get(PREF_BASE) or "album"
        keys = [k for k, _p in self.bases]
        self.base.setCurrentIndex(keys.index(want) if want in keys else 0)
        blab.setBuddy(self.base)
        brow.addWidget(blab)
        brow.addWidget(self.base, 1)
        lay.addLayout(brow)
        self.base.setVisible(len(self.bases) > 1)
        blab.setVisible(len(self.bases) > 1)
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
        self._last = prefs.get(PREF) or ""
        self.fill_patterns(keep=self._last or None)
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
        self.base.currentIndexChanged.connect(lambda _i: (self.fill_patterns(), self.preview()))
        self.preview()

    def folder(self):
        """The folder the pattern reads from (None: each path's end)."""
        key = self.base.currentData()
        return next((p for k, p in self.bases if k == key), None)

    def fill_patterns(self, keep=None):
        """The pattern list: your last pattern, those guessed from the files' names (Read from's
        folder), then the presets that fit how it reads. KEEP: the text to show (else the best
        guess, else what is typed now)."""
        base = self.folder()
        guessed = guess(self.files, base)
        items = list(dict.fromkeys(([self._last] if self._last else []) + guessed +
                                   list(PRESETS if base is None else FOLDER_PRESETS)))
        now = keep if keep is not None else (guessed[0] if guessed else self.pattern.currentText())
        self.pattern.blockSignals(True)
        self.pattern.clear()
        self.pattern.addItems(items)
        if now in items:
            self.pattern.setCurrentIndex(items.index(now))
        else:
            self.pattern.setEditText(now)
        self.pattern.blockSignals(False)

    def text(self) -> str:
        return self.pattern.currentText().strip()

    def preview(self):
        base = self.folder()
        expr = TagMatchExpression(self.text(), self.underscores.isChecked(), anchored=base is not None)
        cols = expr.matched_tags
        self.table.clear()
        self.table.setColumnCount(len(cols) + 1)
        self.table.setHorizontalHeaderLabels([tr("File")] + [display(c) for c in cols])
        self.table.setRowCount(len(self.files))
        self.result_tags = parse(self.files, self.text(), self.underscores.isChecked(), base) if cols else {}
        for i, f in enumerate(self.files):
            name = QTableWidgetItem(covered(f, self.text(), base))      # (the part of the path the pattern reads)
            name.setToolTip(f)
            name.setData(Qt.UserRole, f)
            self.table.setItem(i, 0, name)
            part = read_part(f, base)
            got = expr.match_file(part) if cols and part is not None else {}
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
        if len(self.bases) > 1:
            _set_gui_pref(PREF_BASE, self.base.currentData())
        self.chosen.emit(self.result_tags)
        if self.on_apply:
            self.on_apply(self.result_tags)
        self.accept()


def show_tags_from_filenames(parent, files, on_apply=None, folder=None):
    """Open the sheet over parent's window for these files (FOLDER: their album's folder, for Read
    from); on_apply(overrides) when chosen. Offscreen (tests) the sheet is recorded and not shown;
    the dialog is returned either way."""
    dlg = TagsFromFileNames(parent, files, on_apply, folder)
    if not files:
        sheets.information(parent, tr("Select an album first."),
                           tr("Tags from File and Folder Names reads the names of the selected album's files "
                              "and folders."))
        dlg.deleteLater()
        return None
    sheets.run(dlg, "tags_from_filenames")
    return dlg

