"""gui/preview_panes.py - The Overview's preview as linked panes (0.8.69).

  Your folders (left)        the folders and files as they are on disk, each marked with where it
                             goes ("→ Disc 02", "needs you", "other pressing", "extra")
  Albums as they will be     release → discs (each naming the folder it came from: a set joined
  (middle)                   from disc folders, DSC-10; an SACD layer's folder, MED-22c) → tracks
                             with the file on each; missing tracks greyed, extra files flagged;
                             "Why this release" under it (✓ what agreed, ≠ what differs)
  Tag changes (right)        Tag | Original | New for the selection (a file, a track, a disc, a
                             folder or the whole album); "different in N files" where the files
                             differ; + → − = marks; only the changed part of a value tinted (the
                             style's diff_* tokens); Folder and File name rows; every tag shown,
                             the album's first
  Cover art (bottom)         your scans and the pictures in the tracks, the new front the profile
                             found (Discogs, Cover Art Archive ...), Find Online…, Choose a File…
                             and Generate… (the existing Find Covers sheet and its Make Covers)

Everything shown is read from the plan (plan.preview's own data: each album's files, their diff
rows, identification): nothing is planned or matched here. The trees are Qt models whose children
are made when a row is opened, so a preview of thousands of albums opens at once. Disk work - the
files' current tags for the rows that stay as they are, the pictures for the cover strip - runs on
workers (gui.work.start), a newer selection cancelling the older read.

Editing goes through the inspector's edit path (Inspector.set_tag / set_tag_files, plan.apply_edits):
nothing is written until Apply. Each pane folds to a labelled strip; which are folded, the
widths are remembered per user (gui.json "preview_panes")."""
from __future__ import annotations

import difflib
import html
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import (QAbstractItemModel, QAbstractTableModel, QItemSelectionModel, QMimeData, QModelIndex,
                            QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListView, QListWidget, QListWidgetItem, QMenu, QSizePolicy, QSplitter,
                               QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView,
                               QToolButton, QTreeView, QVBoxLayout, QWidget)

from . import icons, style
from .common import (_gui_prefs, _set_gui_pref, album_words, left_header, planmod, plural, popup_menu,
                     profile_colour, reasons, say)
from .i18n import N_, tr
from .models import (PlanModel, can_apply, fill_roles, status_words, tickable, R_ACCESSIBLETEXT, R_BACKGROUND, R_DECORATION, R_DISPLAY, R_EDIT, R_FONT, R_FOREGROUND, R_TEXTALIGNMENT,
                     R_TOOLTIP, R_USER)
from .tagnames import display as tag_name
from .widgets import Segmented
from .work import start

PREF = "preview_panes"                  # gui.json: {"collapsed": {pane: bool}, "sizes": [..], "vsizes": [..]}
PANES = ("folders", "albums", "tags", "covers")
STRIP = 34                              # a folded side pane: a strip this wide
SIDE_MIN = 100                          # an open side pane's narrowest (960 px wide, the inspector open: three fit)
WHY_MOST = 112                          # "Why this release" at most this tall (it scrolls)
COVER_ROW = 140                         # the cover strip's height
NONE = N_("(none)")
FOLDER, FILENAME = "~folder", "~filename"          # the path rows of the tag pane (never tag keys)
MIME = "application/x-tagmender-folders"           # folders dragged from Your folders (one path a line)
READ_MOST = 200                         # the files whose current tags are read for one selection
CHANGE = ("added", "changed", "removed")
MARK = {"added": "+", "changed": "→", "removed": "−"}
MARK_WORDS = {"+": N_("Added"), "→": N_("Changed"), "−": N_("Removed"), "=": N_("Kept")}
_LAYER = re.compile(r"\((SACD(?: Stereo| Multichannel)?)\)\s*$", re.I)
_DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd)\s*[-_.]?\s*0*(\d+)\b", re.I)


def _ints(v, n):
    return list(v) if isinstance(v, list) and len(v) == n and \
        all(isinstance(x, int) and not isinstance(x, bool) and 0 < x < 100000 for x in v) else None


def clean_prefs(p) -> dict:
    """gui.json's preview_panes as far as it can be used: a value of the wrong kind (edited by
    hand, an older version) is left out, never a reason the window cannot open."""
    p = p if isinstance(p, dict) else {}
    out = {}
    col = p.get("collapsed")
    if isinstance(col, dict):
        out["collapsed"] = {k: v for k, v in col.items() if k in PANES and isinstance(v, bool)}
    for key, n in (("sizes", 3), ("vsizes", 2), ("list", 2)):
        v = _ints(p.get(key), n)
        if v is not None:
            out[key] = v
    return out


# --------------------------------------------------------------------------
# the plan read as folders, discs and tracks (no Qt)
# --------------------------------------------------------------------------
def _vals(v) -> list:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def _num(v):
    """"2", "02", "2/3" -> 2; None when there is no number."""
    s = (_vals(v) or [""])[0].split("/")[0].strip()
    return int(s) if s.isdigit() else None


def new_value(fe, field_) -> list:
    """The value a file is planned to have: its change, else its diff row's, else []."""
    ch = fe.get("changes") or {}
    if field_ in ch:
        return _vals(ch[field_])
    for r in fe.get("diff") or []:
        if r.get("field") == field_:
            return _vals(r.get("after"))
    return []


def old_value(fe, field_) -> list:
    for r in fe.get("diff") or []:
        if r.get("field") == field_:
            return _vals(r.get("before"))
    return []


def _value(fe, field_) -> list:
    return new_value(fe, field_) or old_value(fe, field_)


def layer_of(fe) -> str:
    """MED-22c: "SACD Stereo" when the file goes into an SACD layer's folder ("Disc 01 (SACD
    Stereo)"); "" otherwise."""
    m = _LAYER.search(Path(fe.get("dst") or fe.get("src") or "").parent.name)
    return m.group(1) if m else ""


def disc_of(fe):
    """(disc number or None, layer label) a file is planned as."""
    n = _num(_value(fe, "discnumber"))
    if n is None:
        m = _DISC_DIR.match(Path(fe.get("dst") or "").parent.name)
        n = int(m.group(1)) if m else None
    return n, layer_of(fe)


_LEADING = re.compile(r"^(\d{1,3})(?!\d)")
_NUMBERED = re.compile(r"^\d{1,3}(?!\d)[\s._-]*")      # ("01 - Title": its number left out)


def track_of(fe):
    """The track number a file is planned as (a value the plan leaves as it is has no diff row:
    then the number its file name starts with); None when there is none."""
    n = _num(_value(fe, "tracknumber"))
    if n is None:
        m = _LEADING.match(Path(fe.get("dst") or fe.get("src") or "").name)
        n = int(m.group(1)) % 100 if m and len(m.group(1)) == 3 else int(m.group(1)) if m else None
    return n


def disc_label(key) -> str:
    n, layer = key
    words = tr("Disc {0:02d}").format(n) if n is not None else tr("Tracks")
    return f"{words} ({layer})" if layer else words


def album_title(a) -> str:
    """"Artist – Album" as the album will be tagged (else as found, else its folder)."""
    files = a.get("files") or []
    fe = files[0] if files else {}
    rel = a.get("release") or {}
    title = (_value(fe, "album") or _vals(rel.get("album")) or [""])[0]
    artist = (_value(fe, "albumartist") or _vals(rel.get("albumartist")) or _value(fe, "artist") or [""])[0]
    if not title:
        return Path(a.get("source_dir") or "").name
    return f"{artist} – {title}" if artist else title


def album_facts(a) -> str:
    """"Columbia 478091 2 · AU 1994 · 3 discs": what the release is, from its files' new tags."""
    files = a.get("files") or []
    fe = files[0] if files else {}
    first = lambda k: (_value(fe, k) or [""])[0]  # noqa: E731
    label = " ".join(x for x in (first("label"), first("catalognumber")) if x)
    when = " ".join(x for x in (first("releasecountry"), first("date")[:4]) if x)
    discs = len({disc_of(f) for f in files}) if files else 0
    media = first("media")
    parts = [label, when]
    if discs > 1:
        parts.append(tr("{0} discs").format(discs) + (f" {media}" if media and len(media) < 12 else ""))
    elif media:
        parts.append(media)
    return " · ".join(p for p in parts if p)


def _held(a) -> bool:
    return a.get("status") in ("unresolved", "failed", "conflict") or (a.get("status") == "ready" and planmod.held(a))


def album_discs(a) -> list:
    """The album as it will be: [{"key", "label", "folders", "tracks", "extra"}], discs in order,
    then "Not on the release" (files the release has no track for, files left out by a rule,
    files found with the album that are not of it). Each track {"number", "title", "fe" (None:
    the release's track is not in your folders), "missing", "note"}."""
    files = planmod.plain_files(a) if a.get("status") == planmod.LISTED else a.get("files") or []
    identified = (a.get("identification") or {}).get("status") == "identified"
    discs, extra = {}, []
    for fe in files:
        no = track_of(fe)
        key = disc_of(fe)
        d = discs.setdefault(key, {"key": key, "folders": [], "tracks": [], "total": None})
        parent = str(Path(fe["src"]).parent)
        if parent not in d["folders"]:
            d["folders"].append(parent)
        total = _num(_value(fe, "totaltracks"))
        d["total"] = max(d["total"] or 0, total or 0) or None
        title = (_value(fe, "title") or [_NUMBERED.sub("", Path(fe["src"]).stem) or Path(fe["src"]).stem])[0]
        was = old_value(fe, "title")
        note = tr("title differs") if was and new_value(fe, "title") and was != new_value(fe, "title") else ""
        d["tracks"].append({"number": no, "title": title, "fe": fe, "missing": False, "note": note})
    out = []
    for key in sorted(discs, key=lambda k: (k[0] is None, k[0] or 0, k[1])):
        d = discs[key]
        have = {t["number"] for t in d["tracks"] if t["number"] is not None}
        if identified and d["total"] and a.get("status") not in ("excluded", "skipped"):
            for n in range(1, d["total"] + 1):
                if n not in have:
                    d["tracks"].append({"number": n, "title": tr("Track {0}").format(n), "fe": None, "missing": True,
                                        "note": tr("not in your folders")})
        d["tracks"].sort(key=lambda t: (t["number"] is None, t["number"] or 0, t["title"]))
        d["label"] = disc_label(key)
        out.append(d)
    mine = {fe["src"] for fe in files}
    for s in a.get("skipped") or []:
        if s.get("file") and s["file"] not in mine:
            mine.add(s["file"])
            extra.append({"number": None, "title": Path(s["file"]).name, "fe": {"src": s["file"], "dst": s["file"],
                                                                                "op": "none", "diff": []},
                          "missing": False, "note": tr("left out: {0}").format(s.get("label") or tr("Skipped"))})
    for f in a.get("album_files") or []:
        if f not in mine and identified:
            mine.add(f)
            extra.append({"number": None, "title": Path(f).name, "fe": {"src": f, "dst": f, "op": "none", "diff": []},
                          "missing": False, "note": tr("extra")})
    if extra:
        out.append({"key": ("extra", ""), "label": tr("Not on the release"), "folders": [], "tracks": extra,
                    "total": None, "extra": True})
    return out


_RULE_ID = re.compile(r"\s*\((?:[A-Z]{2,4}-\d+[a-z]?)(?:/[A-Z0-9-]+[a-z]?)*\)")


def plain(text) -> str:
    """Planner words for a person: the matching rules' IDs ("(MED-22c)", "(TRK-4/4b)") left out."""
    return _RULE_ID.sub("", str(text)).strip()


def why_lines(a) -> tuple:
    """("Why this release" or another title, [(mark, words)]): ✓ what agreed, ≠ what differs,
    → what to do (in plain words: no rule IDs)."""
    title, out = _why_lines(a)
    return title, [(m, plain(w)) for m, w in out if plain(w)]


def _why_lines(a) -> tuple:
    ident = a.get("identification") or {}
    out = [("✓", str(x)) for x in ident.get("evidence") or []]
    out += [("≠", str(x)) for x in ident.get("why") or []]
    out += [("≠", str(c)) for c in a.get("conflicts") or []]
    if ident.get("status") != "identified":
        codes, words = reasons.parse(ident.get("reason", "") or "")
        out += [("≠", reasons.describe(c).capitalize()) for c in codes if c != "other"] or \
            ([("≠", words)] if words else [])
    if (ident.get("scans") or {}).get("state") == "differs":
        out.append(("≠", tr("Your scans name another pressing")))
    if a.get("copies"):                                     # VIN-6: shown, never deciding anything
        import dynamics
        out.append(("·", dynamics.words(a["copies"])))
    if _held(a):
        if a.get("held"):
            out.append(("→", str(a["held"])))
        out.append(("→", tr("Choose the release with Change Release, or match the files by hand")))
        return tr("Why it waits for you"), out
    if a.get("status") in ("excluded", "skipped"):
        return tr("Why it is left alone"), out + [("→", a.get("label") or tr("Skipped"))]
    return tr("Why this release"), out


def _join(vals) -> str:
    return "; ".join(vals)


def _mark(action, before, after) -> str:
    if action in CHANGE and before != after:
        return MARK[action]
    return "="


def tag_rows(files, current=None) -> list:
    """The Tag changes rows for FILES (plan file entries): one per tag, then Folder and File
    name. current: {file: its tags as read} for the tags that stay as they are. Each row:
    {"field", "name", "mark", "before", "after", "before_vals", "after_vals", "mixed_before",
    "mixed_after", "n", "mine", "path"}; a value the files do not share is "different in N
    files" (N: the files holding the tag)."""
    import plan as planmod
    current = current or {}
    per = {}
    for fe in files:
        named = set()
        for r in fe.get("diff") or []:
            f = r.get("field")
            if not f or f.startswith("~"):
                continue
            named.add(f)
            b, af = _vals(r.get("before")), _vals(r.get("after"))
            per.setdefault(f, []).append((b, af, _mark(r.get("action"), b, af), r.get("by") == planmod.BY_YOU))
        for f, v in sorted((current.get(fe.get("src")) or {}).items()):
            if f.startswith("~") or f in named or f in (fe.get("changes") or {}):
                continue
            per.setdefault(f, []).append((_vals(v), _vals(v), "=", False))
    # (a tag only some of the files have: the others, read and without it, count as files without
    # a value - the row is not one shared value, and setting it for all asks first. A file not read
    # (past READ_MOST) is not counted: what it holds is not known)
    read = [fe for fe in files if fe.get("src") in current]
    if len(read) > 1:
        for f, entries in per.items():
            have = sum(1 for fe in read if f in _fields_of(fe, current))
            entries += [([], [], "=", False)] * (len(read) - have)
    rows = [_row(f, tag_name(f), e) for f, e in per.items()]
    for key, name, get in ((FOLDER, tr("Folder"), lambda p: str(Path(p).parent)),
                           (FILENAME, tr("File name"), lambda p: Path(p).name)):
        e = []
        for fe in files:
            if fe.get("src"):
                b, af = [get(fe["src"])], [get(fe.get("dst") or fe["src"])]
                e.append((b, af, "→" if b != af else "=", False))
        if e:
            r = _row(key, name, e)
            r["path"] = True
            rows.append(r)
    rows.sort(key=row_order)
    return rows


# the album's own tags, in this order, before the tracks' (title, artist, track number ...): what
# the album is, then where it was released, then its numbering and its database IDs
ALBUM_FIELDS = ("album", "albumartist", "albumartistsort", "date", "originaldate", "originalyear", "releasedate",
                "label", "catalognumber", "barcode", "asin", "genre", "style", "releasecountry", "releasetype",
                "releasestatus", "media", "totaldiscs", "totaltracks", "discsubtitle", "compilation",
                "musicbrainz_albumid", "musicbrainz_releasegroupid", "musicbrainz_albumartistid",
                "discogs_release_id", "discogs_master_id", "discogs_label_id", "discogs_artist_id")
_ALBUM_AT = {f: i for i, f in enumerate(ALBUM_FIELDS)}


def row_order(r):
    """Album-level tags first (ALBUM_FIELDS' order), then the tracks' by name, then Folder and File
    name; the marks say what changes - a row is never moved for it."""
    if r.get("path"):
        return (2, 0 if r["field"] == FOLDER else 1, "")
    f = str(r["field"]).lower()
    return (0, _ALBUM_AT[f], "") if f in _ALBUM_AT else (1, 0, r["name"].lower())


def _fields_of(fe, current) -> set:
    """The tags file entry FE holds or will hold: its diff's, its changes', its tags as read."""
    out = {r.get("field") for r in fe.get("diff") or []}
    out |= set(fe.get("changes") or {})
    out |= set(current.get(fe.get("src")) or {})
    return out


def _mixed_words(values, n) -> str:
    """The cell of a tag the files do not share: "Jazz in 1 of 2 files" when the files holding it
    agree and the others have none, else "different in N files"."""
    held = {v for v in values if v}
    if len(held) == 1:
        k = sum(1 for v in values if v)
        return tr("{value} in {k} of {n} files").format(value=_join(list(next(iter(held)))), k=k, n=n)
    return tr("different in {0} files").format(n)


def _row(field_, name, entries) -> dict:
    befores = {tuple(b) for b, _a, _m, _y in entries}
    afters = {tuple(a) for _b, a, _m, _y in entries}
    marks = {m for _b, _a, m, _y in entries}
    n = len(entries)
    mark = "=" if marks == {"="} else next(iter(marks - {"="})) if len(marks - {"="}) == 1 else "→"
    bv = list(next(iter(befores))) if len(befores) == 1 else None
    av = list(next(iter(afters))) if len(afters) == 1 else None
    return {"field": field_, "name": name, "mark": mark, "n": n, "path": False,
            "mine": any(y for _b, _a, _m, y in entries),
            "before_vals": bv, "after_vals": av, "mixed_before": bv is None, "mixed_after": av is None,
            "before": (_join(bv) or tr(NONE)) if bv is not None else
            _mixed_words([tuple(b) for b, _a, _m, _y in entries], n),
            "after": (_join(av) or tr(NONE)) if av is not None else
            _mixed_words([tuple(a) for _b, a, _m, _y in entries], n)}


def segments(old: str, new: str) -> tuple:
    """([(text, changed)], [(text, changed)]): only the parts of OLD and NEW that differ marked
    (both whole when they have little in common)."""
    if old == new:
        return [(old, False)], [(new, False)]
    if not old or not new:
        return [(old, bool(old))], [(new, bool(new))]
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if sm.ratio() < 0.4:
        return [(old, True)], [(new, True)]
    a, b = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if old[i1:i2]:
            a.append((old[i1:i2], op != "equal"))
        if new[j1:j2]:
            b.append((new[j1:j2], op != "equal"))
    return a, b


def _scope_files(album, kind, key):
    """The file entries a selection stands for (EDT-2: an album not looked up, its own files)."""
    files = planmod.plain_files(album) if (album or {}).get("status") == planmod.LISTED else \
        (album or {}).get("files") or []
    if kind == "album":
        return list(files)
    if kind == "disc":
        return [fe for fe in files if disc_of(fe) == key]
    if kind in ("track", "file"):
        return [fe for fe in files if fe.get("src") == key]
    if kind == "folder":
        k = key.rstrip(os.sep) + os.sep
        return [fe for fe in files if (fe.get("src") or "").startswith(k)]
    return []


# --------------------------------------------------------------------------
# lazy trees
# --------------------------------------------------------------------------
class Node:
    __slots__ = ("kind", "parent", "row", "kids", "album", "data", "ix", "__weakref__")

    def __init__(self, kind, parent, row, album=None, data=None):
        self.kind, self.parent, self.row, self.album, self.data = kind, parent, row, album, data or {}
        self.kids = None
        self.ix = None                                # ({column: (row, its index)}: LazyTree.index_of)


class LazyTree(QAbstractItemModel):
    """A tree whose rows' children are made the first time they are asked for."""
    COLS = ()
    OPENS = ()                                        # the kinds that have children

    def __init__(self):
        super().__init__()
        self.top = Node("top", None, 0)
        self.top.kids = []

    def make_kids(self, node) -> list:
        return []

    def kids(self, node) -> list:
        if node.kids is None:
            node.kids = self.make_kids(node)
            for i, k in enumerate(node.kids):
                k.row, k.parent = i, node
        return node.kids

    def node(self, idx):
        return idx.internalPointer() if idx.isValid() else self.top

    def index(self, row, col, parent=QModelIndex()):
        p = self.node(parent)
        kids = self.kids(p)
        if not (0 <= row < len(kids) and 0 <= col < len(self.COLS)):
            return QModelIndex()
        return self.index_of(kids[row], col)

    def index_of(self, node, col=0):
        """NODE's index in column COL, made once while its row stays (a view asks for the same
        indexes again at each redraw, and each made by Qt let another thread take Python's lock,
        and waited for it back)."""
        if node is None or node is self.top:
            return QModelIndex()
        ix = node.ix
        if ix is None:
            ix = node.ix = {}
        got = ix.get(col)
        if got is not None and got[0] == node.row:
            return got[1]
        made = self.createIndex(node.row, col, node)
        ix[col] = (node.row, made)
        return made

    def parent(self, idx=QModelIndex()):
        if not idx.isValid():
            return QModelIndex()
        p = idx.internalPointer().parent
        return QModelIndex() if p is None or p is self.top else self.index_of(p)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self.kids(self.node(parent)))

    def hasChildren(self, parent=QModelIndex()):
        n = self.node(parent)
        if n is self.top:
            return bool(n.kids)
        if n.kids is not None:
            return bool(n.kids)
        return n.kind in self.OPENS

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def multiData(self, idx, span):                  # (a cell drawn: one call, models.fill_roles)
        fill_roles(self.data, idx, span)

    def headerData(self, s, o, role=R_DISPLAY):
        if o == Qt.Horizontal and role == R_DISPLAY:
            return tr(self.COLS[s])
        return None

    def reset_kids(self, node):
        """NODE's children made again the next time they are asked for (its album changed)."""
        if node.kids:
            self.beginRemoveRows(self.index_of(node), 0, len(node.kids) - 1)
            node.kids = None
            self.endRemoveRows()
        else:
            node.kids = None


CELLS_ONE_BY_ONE = 4                    # (rows changed at most, told cell by cell)


def cells_changed(model, lo, hi, cols):
    """Top rows LO..HI of MODEL changed, in its COLS columns. A few rows: each cell told on its
    own, so the view draws those rows again, not every row it shows (a range of cells is drawn
    again whole: the albums pane's tree asks Python a few hundred times for a full redraw - with
    a look-up running on another thread each call waits for Python's lock - at each album looked
    up). More rows: the range."""
    if hi - lo < CELLS_ONE_BY_ONE:
        for r in range(lo, hi + 1):
            for c in range(cols):
                i = model.index(r, c)
                model.dataChanged.emit(i, i)
    else:
        model.dataChanged.emit(model.index(lo, 0), model.index(hi, cols - 1))


def tint_of(name):
    """The wash of profile NAME's colour (gui.common.profile_colour), or None."""
    return style.profile_tint(profile_colour(name)) if name else None


def _tone(word_kind):
    return {"ok": style.color("identified"), "hold": style.color("attention"), "extra": style.color("attention"),
            "own": style.color("muted"), "missing": style.color("disabled")}.get(word_kind)


class FoldersModel(LazyTree):
    """Your folders as they are on disk: each album's folder (folders that hold several albums
    once), its sub-folders and files, each marked with where it goes."""
    COLS = (N_("Folder or file"), N_("Goes to"))
    OPENS = ("root", "folder")
    profile_of = None                                # (album -> its profile's name: the rows' tint)

    def set_albums(self, albums):
        self.beginResetModel()
        roots = {}
        for a in albums:
            roots.setdefault(album_root(a), []).append(a)
        self.top.kids = [Node("root", self.top, i, data={"path": r, "albums": al})
                         for i, (r, al) in enumerate(roots.items())]
        self.endResetModel()

    def make_kids(self, node):
        if node.kind not in self.OPENS:
            return []
        base = node.data["path"].rstrip(os.sep) + os.sep
        subs, files = {}, []
        for a in node.data["albums"]:
            for e in files_of(a):
                src = e["fe"]["src"]
                if not src.startswith(base):
                    continue
                rest = src[len(base):]
                if os.sep in rest:
                    sub = rest.split(os.sep, 1)[0]
                    d = subs.setdefault(sub, [])
                    if a not in d:
                        d.append(a)
                else:
                    files.append((a, e))
        out = [Node("folder", node, 0, album=al[0] if len(al) == 1 else None, data={"path": base + s, "albums": al})
               for s, al in sorted(subs.items(), key=lambda x: x[0].lower())]
        out += [Node("file", node, 0, album=a, data=e) for a, e in sorted(files, key=lambda x: x[1]["fe"]["src"])]
        return out

    def badge(self, node) -> tuple:
        """(words, tone) beside a folder or file."""
        if node.kind == "file":
            return node.data.get("goes"), node.data.get("tone")
        albums = node.data["albums"]
        if len(albums) > 1:
            held = sum(1 for a in albums if _held(a))
            return (tr("{0} albums").format(len(albums)) + (tr(", {0} need you").format(held) if held else ""),
                    "hold" if held else "ok")
        a = albums[0]
        st = a.get("status")
        if st == "listed":
            return (tr("edited, not saved"), "hold") if planmod.plain_pending(a) else (tr("not looked up"), "own")
        if st in ("excluded", "skipped"):
            return tr("left alone"), "own"
        if st == "done":
            return tr("done"), "ok"
        if ((a.get("identification") or {}).get("scans") or {}).get("state") == "differs":
            return tr("other pressing"), "hold"
        if _held(a):
            return tr("needs you"), "hold"
        if node.kind == "folder":
            keys = {disc_of(e["fe"]) for e in files_of(a) if e["fe"]["src"].startswith(node.data["path"] + os.sep)
                    and e.get("disc")}
            discs = {disc_of(fe) for fe in a.get("files") or []}
            if len(keys) == 1 and len(discs) > 1:
                return "→ " + disc_label(next(iter(keys))), "ok"
        return tr("1 album"), "ok"

    def data(self, idx, role=R_DISPLAY):
        if not idx.isValid():
            return None
        n, c = idx.internalPointer(), idx.column()
        if role == R_DISPLAY:
            if c == 0:
                return n.data["fe"]["src"].rsplit(os.sep, 1)[-1] if n.kind == "file" else \
                    (album_words(n.data["path"]) if n.kind == "root" else n.data["path"].rsplit(os.sep, 1)[-1])
            return self.badge(n)[0] or ""
        if role == R_TOOLTIP:
            return n.data["fe"]["src"] if n.kind == "file" else n.data["path"]
        if role == R_FOREGROUND and c == 1:
            t = _tone(self.badge(n)[1])
            return QColor(t) if t else None
        if role == R_FONT and n.kind == "root" and c == 0:
            f = style.font("body")
            f.setWeight(QFont.Weight(600))
            return f
        if role == R_DECORATION and c == 0:
            return icons.icon("folder-open" if n.kind != "file" else "tag", "muted", 14)
        if role == R_BACKGROUND and self.profile_of is not None:
            albums = [n.album] if n.kind == "file" else n.data["albums"]
            names = {self.profile_of(a) for a in albums if a is not None}
            return tint_of(next(iter(names))) if len(names) == 1 else None
        if role == R_ACCESSIBLETEXT:
            words = self.badge(n)[0]
            return f"{self.data(idx.siblingAtColumn(0))}, {words}" if words else self.data(idx.siblingAtColumn(0))
        return None

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.internalPointer().kind in ("root", "folder"):
            f |= Qt.ItemIsDragEnabled
        return f

    def mimeTypes(self):
        return [MIME, "text/uri-list"]

    def mimeData(self, idxs):
        paths = sorted({i.internalPointer().data["path"] for i in idxs
                        if i.isValid() and i.internalPointer().kind in ("root", "folder")})
        md = QMimeData()
        md.setData(MIME, "\n".join(paths).encode())
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return md

    def supportedDragActions(self):
        return Qt.LinkAction | Qt.CopyAction


def album_root(a) -> str:
    """The folder an album's files are found under: its own, or (files outside it: a set joined
    from folders side by side) the folder that holds them all."""
    root = str(a.get("source_dir") or "").rstrip(os.sep) or os.sep
    base = root + os.sep
    srcs = [fe.get("src") or "" for fe in a.get("files") or [] if not fe.get("joined")]   # (DSC-12)
    if all(s.startswith(base) for s in srcs):
        return root
    try:
        return os.path.commonpath([root] + [os.path.dirname(s) or "." for s in srcs])
    except ValueError:
        return root


_FILES: dict = {}                       # id(album) -> (album, stamp, files_of): never kept in the plan itself


def forget(album=None):
    """files_of's memory of ALBUM (None: of every album) dropped."""
    if album is None:
        _FILES.clear()
    else:
        _FILES.pop(id(album), None)


def files_of(a) -> list:
    """Every file an album shows in Your folders, with where it goes: [{"fe", "goes", "tone",
    "disc"}] (remembered until the album's files change)."""
    if a.get("status") == planmod.LISTED:
        planmod.plain_files(a)                        # (EDT-2: its files, to select and edit)
    stamp = id(a.get("files")), len(a.get("files") or [])
    hit = _FILES.get(id(a))
    if hit is not None and hit[0] is a and hit[1] == stamp:
        return hit[2]
    out = []
    multi = len({disc_of(fe) for fe in a.get("files") or []}) > 1
    held = _held(a)
    for d in album_discs(a):
        for t in d["tracks"]:
            fe = t["fe"]
            if fe is None:
                continue
            if d.get("extra"):
                out.append({"fe": fe, "goes": t["note"], "tone": "extra", "disc": False})
            elif a.get("status") == "listed" and fe.get("op", "none") != "none":
                out.append({"fe": fe, "goes": tr("edited"), "tone": "hold", "disc": True})   # (EDT-2: not saved)
            elif held or a.get("status") in ("listed", "excluded", "skipped"):
                out.append({"fe": fe, "goes": "", "tone": "own", "disc": True})
            else:
                no = t["number"]
                where = tr("{disc} #{track}").format(disc=d["label"], track=no) if multi and no else \
                    d["label"] if multi else tr("track {0}").format(no) if no else ""
                out.append({"fe": fe, "goes": "→ " + where if where else "", "tone": "ok", "disc": True})
    _FILES[id(a)] = (a, stamp, out)
    return out


SUB = R_USER + 5                        # the muted words after a row's name ("← Disc 01", the release's facts)
FACTS = R_USER + 6                      # an album row's release facts alone (SUB without its profile)
PROFILE = R_USER + 7                    # an album row's profile (its name, after a dot of its colour)


def _stamp(a) -> tuple:
    """What an album row's discs and tracks are made from: when it changes they are made again."""
    a = a or {}
    return id(a), id(a.get("files")), len(a.get("files") or []), a.get("status"), id(a.get("identification"))


class AlbumsModel(LazyTree):
    """The albums as they will be: one top row per album of the Overview's list - ROWS, its sorted
    and filtered rows (gui.models.SortedRows): its order, its filter, its selection - and under each
    its discs (each naming the folder it came from) and their tracks (each with its file). The
    album rows show the list's Status and Score; their check box is the list's (ticked: applied)."""
    COLS = (N_("Albums as they will be"), N_("Status"), N_("Score"))
    NAME, STATUS, SCORE = 0, 1, 2
    SRC = {0: 1, 1: 0, 2: 3}                        # (a column -> the list's column it shows and sorts by)
    OPENS = ("album", "disc")
    join_asked = Signal(object, list)               # (album, folders dropped on it)

    def __init__(self, rows=None):
        super().__init__()
        if rows is None:
            from .models import PlanModel, SortedRows
            rows = SortedRows(self)
            rows.setSourceModel(PlanModel())
        self.rows = rows
        self._gone = []                              # (nodes taken out: kept until the next reset)
        self._persist = None
        rows.modelAboutToBeReset.connect(self.beginResetModel)
        rows.modelReset.connect(self._reset)
        rows.rowsAboutToBeInserted.connect(lambda _p, a, b: self.beginInsertRows(QModelIndex(), a, b))
        rows.rowsInserted.connect(self._inserted)
        rows.rowsAboutToBeRemoved.connect(lambda _p, a, b: self.beginRemoveRows(QModelIndex(), a, b))
        rows.rowsRemoved.connect(self._removed)
        rows.layoutAboutToBeChanged.connect(self._relaying)
        rows.layoutChanged.connect(self._relaid)
        rows.dataChanged.connect(self._changed)
        self.top.kids = self._nodes(0, rows.rowCount() - 1)

    # ---- the list's rows, followed --------------------------------------------------------------
    def album_at(self, r):
        albums = getattr(self.rows.sourceModel(), "albums", None) or []
        order = getattr(self.rows, "_rows", None)          # (SortedRows: its rows' source rows, read
        if isinstance(order, list):                       # directly - asked of every row at each change)
            s = order[r] if 0 <= r < len(order) else -1
            return albums[s] if 0 <= s < len(albums) else None
        src = self.rows.mapToSource(self.rows.index(r, 0))
        return albums[src.row()] if src.isValid() and 0 <= src.row() < len(albums) else None

    def _nodes(self, a, b) -> list:
        out = []
        for r in range(a, b + 1):
            al = self.album_at(r)
            out.append(Node("album", self.top, r, album=al, data={"stamp": _stamp(al)}))
        return out

    def _number(self, start=0):
        for i in range(start, len(self.top.kids)):
            self.top.kids[i].row = i

    def _reset(self):
        self._gone = []
        self.top.kids = self._nodes(0, self.rows.rowCount() - 1)
        self.endResetModel()

    def _inserted(self, _p, a, b):
        self.top.kids[a:a] = self._nodes(a, b)
        self._number(a)
        self.endInsertRows()

    def _removed(self, _p, a, b):
        self._gone += self.top.kids[a:b + 1]
        del self.top.kids[a:b + 1]
        self._number(a)
        self.endRemoveRows()

    def _relaying(self):
        self.layoutAboutToBeChanged.emit()
        self._persist = list(self.persistentIndexList())

    def _relaid(self):
        by_id = {id(n.album): n for n in self.top.kids}
        kids = []
        for r in range(self.rows.rowCount()):
            al = self.album_at(r)
            n = by_id.pop(id(al), None) or Node("album", self.top, r, album=al, data={"stamp": _stamp(al)})
            kids.append(n)
        self._gone += list(by_id.values())
        self.top.kids = kids
        self._number()
        tops = {id(n) for n in kids}
        old, new = [], []
        for i in self._persist or []:
            n = i.internalPointer() if i.isValid() else None
            if n is not None and n.parent is self.top:
                old.append(i)
                new.append(self.createIndex(n.row, i.column(), n) if id(n) in tops else QModelIndex())
        self._persist = None
        if old:
            self.changePersistentIndexList(old, new)
        self.layoutChanged.emit()

    def _changed(self, top, bottom, *_):
        lo, hi = max(0, top.row()), min(len(self.top.kids) - 1, bottom.row())
        for r in range(lo, hi + 1):
            n = self.top.kids[r]
            n.data.pop("memo", None)                     # (what its row shows: asked again)
            al = self.album_at(r)
            st = _stamp(al)
            if n.album is not al or n.data.get("stamp") != st:
                n.album, n.data["stamp"] = al, st
                self._refresh_kids(n)
        if lo <= hi:
            cells_changed(self, lo, hi, len(self.COLS))

    def _refresh_kids(self, n):
        """Album row N's entry changed (your edit, a look-up): its discs and tracks read again - in
        place when they are as many as before, so a disc or track selected stays selected; else
        made again when next opened."""
        if n.kids is None:
            return
        new = album_discs(n.album or {})
        old = n.kids
        if len(new) != len(old) or any(d.kids is not None and len(d.kids) != len(nd["tracks"])
                                       for d, nd in zip(old, new)):
            self.reset_kids(n)
            return
        last = len(self.COLS) - 1
        for d, nd in zip(old, new):
            d.album, d.data = n.album, nd
            if d.kids:
                for t, nt in zip(d.kids, nd["tracks"]):
                    t.album, t.data = n.album, nt
                self.dataChanged.emit(self.index_of(d.kids[0]), self.index_of(d.kids[-1], last))
        if old:
            self.dataChanged.emit(self.index_of(old[0]), self.index_of(old[-1], last))

    def node_of(self, album):
        return next((n for n in self.top.kids if n.album is album), None)

    def forget_looks(self):
        """What the album rows show asked again when next drawn (the scheme, a profile's colour)."""
        for n in self.top.kids:
            n.data.pop("memo", None)

    def sort(self, column, order=Qt.AscendingOrder):
        self.rows.sort(self.SRC.get(column, 0), order)

    # ---- the rows' children --------------------------------------------------------------------
    def make_kids(self, node):
        if node.kind == "album":
            return [Node("disc", node, 0, album=node.album, data=d) for d in album_discs(node.album or {})]
        if node.kind == "disc":
            return [Node("track", node, 0, album=node.album, data=t) for t in node.data["tracks"]]
        return []

    def _list(self, n, col, role):
        """What the list says in its column COL for album row N (asked of the list's own model
        directly when it can be: no index of the sorted rows made for it)."""
        order = getattr(self.rows, "_rows", None)
        src = self.rows.sourceModel()
        if isinstance(order, list) and isinstance(src, PlanModel) and 0 <= n.row < len(order):
            return src.data(src.cell(order[n.row], col), role)
        return self.rows.data(self.rows.index(n.row, col), role)

    def album_tip(self, n) -> str:
        a = n.album or {}
        lines = [a.get("source_dir") or ""]
        for col in (2, 4, 5, 7):                    # (Source, Changes, Why, Scans: the old list's columns)
            v = self._list(n, col, R_DISPLAY)
            if v:
                lines.append(f"{tr(self.rows.headerData(col, Qt.Horizontal))}: {v}")
        prof = self._list(n, 6, R_DISPLAY)
        if prof:
            lines.append(tr("Profile: {0}").format(prof))
        return "<p style='white-space:pre-wrap'>" + html.escape("\n".join(x for x in lines if x)) + "</p>"

    def data(self, idx, role=R_DISPLAY):
        """A cell's ROLE. An album row's are remembered until its row changes (the list's
        dataChanged: a look-up, your edit, a profile chosen) or forget_looks: a row is drawn
        again at every change of any row, and asks a dozen roles of each of its cells, many of
        them through the list (its profile, its tick)."""
        if not idx.isValid():
            return None
        n = idx.internalPointer()
        if role == R_USER:
            return n.album or {}
        if role == R_BACKGROUND:
            top = n
            while top.parent is not self.top and top.parent is not None:
                top = top.parent
            memo = top.data.get("memo")
            if memo is None:
                memo = top.data["memo"] = {}
            if "tint" not in memo:
                # (an album applied: its row, and its discs' and tracks', on the applied wash - the
                # badge and the word say it too, so the wash is never the only sign)
                memo["tint"] = QColor(style.color("applied_bg")) if (top.album or {}).get("status") == "done" \
                    else tint_of(self._list(top, 6, R_DISPLAY))
            return memo["tint"]
        if n.kind != "album":
            return self._data(idx, n, role)
        memo = n.data.get("memo")
        if memo is None:
            memo = n.data["memo"] = {}
        key = (idx.column(), role)
        if key not in memo:
            memo[key] = self._data(idx, n, role)
        return memo[key]

    def _data(self, idx, n, role):
        c = idx.column()
        a = n.album or {}
        if n.kind == "album":
            if c == self.NAME:
                if role == R_DISPLAY:
                    return album_title(a)
                if role == SUB:                         # (the profile in words: never its tint alone)
                    return " · ".join(x for x in (album_facts(a), self._list(n, 6, R_DISPLAY)) if x)
                if role == FACTS:
                    return album_facts(a)
                if role == PROFILE:
                    return self._list(n, 6, R_DISPLAY) or ""
                if role == R_ACCESSIBLETEXT:
                    sub = self.data(idx, SUB)
                    return f"{album_title(a)}, {sub}" if sub else album_title(a)
                if role == R_TOOLTIP:
                    return self.album_tip(n)
                if role == R_FONT:
                    f = style.font("body")
                    f.setWeight(QFont.Weight(600))
                    return f
                if role == int(Qt.CheckStateRole):
                    return self._list(n, 1, role)
                if role == R_DECORATION and a.get("status") == "done":
                    # (applied: a filled check badge where its include box was - not a box to tick)
                    return icons.badge("circle-check", "applied", "on_applied", 16)
                return None
            src = self.SRC[c]
            if c == self.SCORE and role in (R_FONT, R_FOREGROUND):
                # (plain numbers in the secondary colour, as the mockup: the status carries the
                # symbol and the word; how far to trust the score is in its tooltip and read out)
                return QColor(style.color("muted")) if role == R_FOREGROUND else None
            if c == self.STATUS and role == R_FOREGROUND:
                if self._list(n, 0, R_DISPLAY) != status_words(a):        # (Waiting…, Applying…)
                    return QColor(style.color("muted"))
                return QColor(style.color("applied")) if a.get("status") == "done" else \
                    style.status_color(a.get("status"))
            return self._list(n, src, role)
        if n.kind == "disc":
            d = n.data
            if c == self.NAME and role in (R_DISPLAY, R_ACCESSIBLETEXT, SUB):
                folders = d["folders"]
                src = ("← " + Path(folders[0]).name + (tr(" and {0} more").format(len(folders) - 1)
                                                        if len(folders) > 1 else "")) if folders else ""
                if role == SUB:
                    return src
                return d["label"] if role == R_DISPLAY else f"{d['label']}, {src}"
            if c == self.STATUS and role in (R_DISPLAY, R_ACCESSIBLETEXT):
                have = sum(1 for t in d["tracks"] if t["fe"] is not None)
                return tr("{0} of {1}").format(have, len(d["tracks"])) if not d.get("extra") else \
                    plural(have, tr("file"), tr("files"))
            if role == R_TOOLTIP and c == self.NAME:
                return "\n".join(d["folders"]) or None
            if role == R_FOREGROUND and c == self.STATUS:
                return QColor(style.color("muted"))
            if role == R_FONT and c == self.NAME:
                f = style.font("body")
                f.setWeight(QFont.Weight(600))
                return f
            return None
        t = n.data
        if c == self.NAME and role in (R_DISPLAY, R_ACCESSIBLETEXT, SUB):
            num = f"{t['number']:02d}  " if t["number"] else ""
            src = "← " + Path(t["fe"]["src"]).name if t["fe"] else ""
            if role == SUB:
                return src
            return num + t["title"] if role == R_DISPLAY else ", ".join(x for x in (num + t["title"], src, t["note"]) if x)
        if c == self.STATUS and role in (R_DISPLAY, R_ACCESSIBLETEXT):
            return t["note"]
        if role == R_TOOLTIP and t["fe"]:
            return t["fe"]["src"]
        if role == R_FOREGROUND:
            if t["missing"]:
                return QColor(style.color("disabled"))
            if c == self.STATUS and t["note"]:
                return QColor(style.color("attention"))
        return None

    def headerData(self, s, o, role=R_DISPLAY):
        if o == Qt.Horizontal and role == R_TEXTALIGNMENT:
            return int((Qt.AlignRight if s == self.SCORE else Qt.AlignLeft) | Qt.AlignVCenter)
        return super().headerData(s, o, role)

    def flags(self, idx):
        n = idx.internalPointer() if idx.isValid() else None
        memo = n.data.get("memo") if n is not None and n.kind == "album" else None
        if memo is None and n is not None and n.kind == "album":
            memo = n.data["memo"] = {}
        key = ("flags", idx.column())
        if memo is not None and key in memo:
            return memo[key]
        if n is None or n.kind != "album":
            return super().flags(idx)
        # (an album row's: worked out here, not asked of Qt and the list - the tree asks each of
        # its rows as it lays them out, a call into Python each)
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled
        if idx.column() == self.NAME:
            if isinstance(self.rows.sourceModel(), PlanModel):
                if n.album is not None and tickable(n.album):
                    f |= Qt.ItemIsUserCheckable                 # (as the list's: ticked, applied or asked)
            else:
                f |= self.rows.flags(self.rows.index(idx.row(), 1)) & Qt.ItemIsUserCheckable
        memo[key] = f
        return f

    def setData(self, idx, value, role=R_EDIT):
        if idx.isValid() and role == int(Qt.CheckStateRole) and idx.internalPointer().kind == "album" \
                and idx.column() == self.NAME:
            return self.rows.setData(self.rows.index(idx.row(), 1), value, role)
        return False

    def mimeTypes(self):
        return [MIME]

    def supportedDropActions(self):
        return Qt.LinkAction | Qt.CopyAction | Qt.MoveAction

    def canDropMimeData(self, md, action, row, col, parent):
        return md.hasFormat(MIME) and parent.isValid() and parent.internalPointer().kind == "album"

    def dropMimeData(self, md, action, row, col, parent):
        if not self.canDropMimeData(md, action, row, col, parent):
            return False
        paths = [p for p in bytes(md.data(MIME)).decode().splitlines() if p]
        self.join_asked.emit(parent.internalPointer().album, paths)
        return False                                   # (nothing moves here: the sheet does it)


class NameDelegate(QStyledItemDelegate):
    """A row's name, then its muted words (SUB: "← Disc 01", the release's facts), cut at the end."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name, sub = opt.text, index.data(SUB) or ""
        prof = index.data(PROFILE) or ""
        if prof:                                        # (an album: its profile's dot and name, then its facts)
            sub = index.data(FACTS) or ""
        opt.text = ""
        w = opt.widget
        st = w.style() if w else QApplication.style()
        st.drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        r = st.subElementRect(QStyle.SE_ItemViewItemText, opt, w).adjusted(4, 0, -4, 0)
        sel = bool(option.state & QStyle.State_Selected)
        fg = index.data(R_FOREGROUND)
        ink = option.palette.highlightedText().color() if sel else (QColor(fg) if fg is not None else
                                                                    option.palette.text().color())
        painter.save()
        painter.setFont(opt.font)
        fm = painter.fontMetrics()
        # (an album's profile, its dot and name, is never cut away: the name gives it its room)
        pw = (13 + QFontMetrics(style.font("body")).horizontalAdvance(prof) + 10) if prof else 0
        room = max(r.width() // 2, r.width() - pw) if prof else r.width()
        shown = fm.elidedText(name, Qt.ElideRight, room)
        painter.setPen(ink)
        painter.drawText(r, int(Qt.AlignLeft | Qt.AlignVCenter), shown)
        used = fm.horizontalAdvance(shown) + 10
        if prof and used + 20 < r.width():
            self.paint_profile(painter, r.adjusted(used, 0, 0, 0), prof, sel)
            used += pw
        if sub and shown == name and used < r.width():
            f = style.font("body")
            painter.setFont(f)
            muted = QColor(style.color("on_selection")) if sel else QColor(style.color("muted"))
            if sel:
                muted.setAlpha(200)
            painter.setPen(muted)
            rest = r.adjusted(used, 0, 0, 0)
            painter.drawText(rest, int(Qt.AlignLeft | Qt.AlignVCenter),
                             painter.fontMetrics().elidedText(sub, Qt.ElideRight, rest.width()))
        painter.restore()

    @staticmethod
    def paint_profile(painter, r, prof, sel):
        """A Finder-style dot of the profile's colour, then its name (muted): the profile reads
        in both schemes without its row's wash."""
        dot = style.profile_dot(profile_colour(prof))
        x = r.left()
        if dot is not None:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QColor(style.color("on_selection")) if sel else Qt.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(x, r.center().y() - 4, 8, 8)
            painter.restore()
            x += 13
        painter.setFont(style.font("body"))
        muted = QColor(style.color("on_selection")) if sel else QColor(style.color("muted"))
        painter.setPen(muted)
        rest = r.adjusted(x - r.left(), 0, 0, 0)
        painter.drawText(rest, int(Qt.AlignLeft | Qt.AlignVCenter),
                         painter.fontMetrics().elidedText(prof, Qt.ElideRight, rest.width()))


class AlbumsView(QTreeView):
    """The albums pane's tree, with the calls the Overview's list made (selectRow: an album's row)."""

    def selectRow(self, row):
        idx = self.model().index(row, 0)
        if idx.isValid():
            self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

    def horizontalHeader(self):
        return self.header()


# --------------------------------------------------------------------------
# tag changes
# --------------------------------------------------------------------------
_COLOURS = {}                           # (theme colour name, its value) -> QColor: made once per theme


def _colour(name) -> QColor:
    """style.color(NAME) as a QColor, made once for each value it has (a theme change: a new one)."""
    value = style.color(name)
    got = _COLOURS.get((name, value))
    if got is None:
        got = _COLOURS[(name, value)] = QColor(value)
    return got


class TagModel(QAbstractTableModel):
    COLS = ("", N_("Tag"), N_("Original"), N_("New"))
    SEGMENTS = R_USER + 3                         # [(text, changed)] for the delegate
    EDIT_ROLE = R_USER + 1

    def __init__(self):
        super().__init__()
        self.rows, self.editable, self.typed = [], False, None

    def multiData(self, idx, span):                  # (a cell drawn: one call, models.fill_roles)
        fill_roles(self.data, idx, span)

    def set_rows(self, rows, editable):
        self.beginResetModel()
        self.rows, self.editable = rows, editable
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, s, o, role=R_DISPLAY):
        if o == Qt.Horizontal and role == R_DISPLAY:
            return tr(self.COLS[s]) if self.COLS[s] else ""
        if o == Qt.Horizontal and role == R_TEXTALIGNMENT:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if o == Qt.Horizontal and role == R_TOOLTIP and s == 0:
            return tr("Change: + added, → changed, − removed, = kept")
        return None

    def can_edit(self, r) -> bool:
        """The row may be changed (Edit Value…, Revert, Remove): a tag, not a path."""
        return self.editable and not r.get("path")

    def inline(self, r) -> bool:
        """Typed in its cell: only a value every file shares, of one value at most (several
        values, or files that differ: Edit Value…, as in the inspector)."""
        return self.can_edit(r) and not r["mixed_after"] and len(r["after_vals"] or []) <= 1

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.column() == 3 and self.inline(self.rows[idx.row()]):
            f |= Qt.ItemIsEditable
        return f

    def setData(self, idx, value, role=R_EDIT):
        if role != R_EDIT or not idx.isValid() or idx.column() != 3 or self.typed is None:
            return False
        self.typed(self.rows[idx.row()], str(value or ""))
        return True

    def data(self, idx, role=R_DISPLAY):
        if not idx.isValid():
            return None
        r, c = self.rows[idx.row()], idx.column()
        if role in (R_DISPLAY, R_ACCESSIBLETEXT):
            if c == 0:
                return r["mark"] if role == R_DISPLAY else tr(MARK_WORDS[r["mark"]])
            return (None, r["name"], r["before"], r["after"])[c]
        if role == R_TOOLTIP:
            if c == 0:
                return tr(MARK_WORDS[r["mark"]]) + (tr(" · your edit") if r["mine"] else "")
            if c in (2, 3):
                return (r["before"] if c == 2 else r["after"]) + ("\n" + tr("Double-click to edit the new value")
                                                                  if c == 3 and self.can_edit(r) else "")
            return f"{r['name']} ({r['field']})" if not r.get("path") else r["name"]
        if role == R_FOREGROUND and c == 0:
            return _colour({"+": "accent", "→": "attention", "−": "error"}.get(r["mark"], "muted"))
        if role == R_FOREGROUND and c in (1, 3) and r["mark"] == "=":
            return _colour("muted")                         # (kept: its new value the same, dimmed)
        if role == R_TEXTALIGNMENT and c == 0:
            return int(Qt.AlignCenter)
        if role == R_FONT and r["mine"] and c in (1, 3):
            f = style.font("body")
            f.setItalic(True)
            return f
        if role == self.EDIT_ROLE:
            return (r["after_vals"] or [""])[0] if self.inline(r) else ""
        if role == self.SEGMENTS and c in (2, 3):
            mixed = r["mixed_before"] if c == 2 else r["mixed_after"]
            if mixed or r["mark"] == "=":
                return None
            old, new = r["before"], r["after"]
            if r["mark"] == "+":
                return [(new, True)] if c == 3 else None
            if r["mark"] == "−":
                return [(old, True)] if c == 2 else None
            a, b = segments(old, new)
            return a if c == 2 else b
        return None


class DiffDelegate(QStyledItemDelegate):
    """A value with only its changed part tinted: new text on the blue wash, the old text it
    replaces struck through on the orange (style tokens diff_*); a value the files do not share
    in muted italics."""

    def paint(self, painter, option, index):
        segs = index.data(TagModel.SEGMENTS)
        model = index.model()
        row = model.rows[index.row()] if hasattr(model, "rows") else {}
        mixed = row.get("mixed_before" if index.column() == 2 else "mixed_after")
        if not segs and not mixed:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text_rect = opt.rect.adjusted(6, 0, -4, 0)
        opt.text = ""
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        t = style.tokens()
        old = index.column() == 2
        if mixed:
            body = (f"<i style='color:{t['muted']}'>{html.escape(index.data(R_DISPLAY) or '')}</i>")
        else:
            bg, ink = (t["diff_old_bg"], t["diff_old_ink"]) if old else (t["diff_new_bg"], t["diff_new_ink"])
            strike = "text-decoration:line-through;" if old else ""
            body = "".join(f"<span style='background:{bg};color:{ink};{strike}'>{html.escape(s)}</span>" if ch
                           else html.escape(s) for s, ch in segs)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        sel = bool(option.state & QStyle.State_Selected)
        ink = option.palette.highlightedText().color().name() if sel else option.palette.text().color().name()
        doc.setHtml(f"<div style='color:{ink};white-space:pre'>{body}</div>")
        painter.save()
        painter.setClipRect(text_rect)
        painter.translate(text_rect.left(), text_rect.top() + max(0, (text_rect.height() - doc.size().height()) / 2))
        doc.drawContents(painter)
        painter.restore()

    def setEditorData(self, editor, index):
        editor.setText(index.data(TagModel.EDIT_ROLE) or "")


# --------------------------------------------------------------------------
# a pane that folds to a strip
# --------------------------------------------------------------------------
class ElidedLabel(QLabel):
    """One line, cut with "…" where the pane is too narrow; the whole text in its tooltip."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def elided(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.contentsRect().width()))

    def paintEvent(self, _e):
        p = QPainter(self)
        align = (self.alignment() & Qt.AlignHorizontal_Mask) or Qt.AlignLeft
        self.style().drawItemText(p, self.contentsRect(), int(align | Qt.AlignVCenter), self.palette(),
                                  self.isEnabled(), self.elided(), self.foregroundRole())
        p.end()


class FitPicture(QLabel):
    """A picture as large as its room allows (at most MOST px a side), square, centred; its words
    (no picture) in the middle."""

    def __init__(self, most=180, least=72):
        super().__init__()
        self.most, self._px = most, QPixmap()
        self.setMinimumSize(least, least)
        self.setMaximumHeight(most)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignCenter)

    def setPixmap(self, px):
        self._px = QPixmap(px)
        self.setText("")
        self.update()

    def pixmap_shown(self):
        return self._px

    def sizeHint(self):
        return QSize(self.most, self.most)

    def paintEvent(self, e):
        if self._px.isNull():
            return super().paintEvent(e)
        side = min(self.width(), self.height(), self.most)
        x, y = (self.width() - side) // 2, (self.height() - side) // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(x, y, side, side, self._px.scaled(side * 2, side * 2, Qt.KeepAspectRatio,
                                                      Qt.SmoothTransformation))
        p.end()


class Pane(QFrame):
    """A pane of the window: its title in small capitals and a word or count at the right of the
    title row (subtitle), its controls (extras) between them, its content below. Panes meet at
    hairlines, as a Mac window's: no box of their own."""

    def __init__(self, key, title, subtitle="", head_height=30):
        super().__init__()
        self.key, self.title = key, title
        self.setObjectName("previewPane")
        self.setFrameShape(QFrame.NoFrame)
        self.setAccessibleName(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("paneHead")
        self.head.setFixedHeight(head_height)
        hl = self.head_lay = QHBoxLayout(self.head)
        hl.setContentsMargins(12, 0, 12, 0)
        hl.setSpacing(12)
        self.title_label = QLabel(title)
        f = style.font("caption")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight(600))
        f.setCapitalization(QFont.AllUppercase)
        self.title_label.setFont(f)
        self.title_label.setProperty("role", "caption")
        self.title_label.setAccessibleName(title)
        hl.addWidget(self.title_label)
        self.extras = QHBoxLayout()
        self.extras.setSpacing(8)
        hl.addLayout(self.extras)
        self.subtitle = ElidedLabel(subtitle)
        self.subtitle.setProperty("role", "caption")
        self.subtitle.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(self.subtitle, 1)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)
        lay.addWidget(self.body, 1)

    def add(self, w, stretch=0):
        self.body_lay.addWidget(w, stretch)

    def add_extra(self, w, words=False):
        self.extras.addWidget(w)


# --------------------------------------------------------------------------
# cover art
# --------------------------------------------------------------------------
class ThumbDelegate(QStyledItemDelegate):
    """A cover's thumbnail alone (its words are the item's tooltip and accessible text), the one
    shown large ringed in the accent."""

    def paint(self, painter, option, index):
        r = option.rect.adjusted(3, 3, -3, -3)
        icon = index.data(R_DECORATION)
        painter.save()
        if isinstance(icon, QIcon):
            icon.paint(painter, r)
        if option.state & QStyle.State_Selected:
            from PySide6.QtGui import QPen
            painter.setPen(QPen(QColor(style.color("accent")), 2))
            painter.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 3, 3)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(40, 40)


class CoverStrip(QListWidget):
    """The covers there are for the album, as thumbnails in a row."""

    def __init__(self):
        super().__init__()
        self.setObjectName("coverStrip")
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Static)
        self.setIconSize(QSize(34, 34))
        self.setGridSize(QSize(40, 40))
        self.setSpacing(0)
        self.setItemDelegate(ThumbDelegate(self))
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(46)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAccessibleName(tr("Covers for the album"))


class CoverPanel(QFrame):
    """The cover side of the inspector pane: an image file or an image link dropped here becomes
    the album's new cover (the inspector's own path, as its Artwork tab took one); music dropped
    here is for the window."""

    def __init__(self, panes):
        super().__init__()
        self.panes = panes
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        from .common import music_paths
        from .inspector import IMAGE_SUFFIXES, image_link
        md = e.mimeData()
        img = any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES
                  or u.scheme() in ("http", "https") for u in (md.urls() if md.hasUrls() else [])) or \
            (md.hasText() and bool(image_link(md.text())))
        if music_paths(md) or (img and self.panes.cover_editable()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        from .common import music_paths
        music = music_paths(e.mimeData())
        if music and hasattr(self.window(), "dropped"):
            e.acceptProposedAction()
            self.window().dropped(music)
            return
        insp = self.panes._insp()
        if insp is None or not self.panes.cover_editable():
            return e.ignore()
        path, url = insp.art._dropped(e.mimeData())
        if not (path or url):
            return e.ignore()
        e.acceptProposedAction()
        album = self.panes.scope[0]
        if insp.album is not album:
            insp.show_album(album, getattr(self.panes.owner, "plan", None))
        insp.cover_from_file(path) if path else insp.cover_from_url(url)


def _placeholder(words) -> QIcon:
    px = QPixmap(192, 192)
    px.fill(QColor(style.color("alt")))
    p = QPainter(px)
    p.setPen(QColor(style.color("muted")))
    f = style.font("headline")
    f.setPixelSize(28)
    p.setFont(f)
    p.drawText(px.rect(), int(Qt.AlignCenter), words)
    p.end()
    return QIcon(px)


# --------------------------------------------------------------------------
# the panes together
# --------------------------------------------------------------------------
INSPECTOR_TABS = (("tags", N_("Tags")), ("why", N_("Why This Release")))
COVER_SIDE = 180                       # the cover shown large, px a side
COVER_PANE = 300                       # the cover pane's width (narrower in a narrow window)


class PreviewPanes(QWidget):
    """The Overview's content: Your files | Albums as they will be, over the inspector (Tags or
    Why This Release, beside the Cover). owner: the Overview (its window's inspector edits, Change
    Release and Match Files Manually); rows: its list's sorted and filtered rows, which the albums
    pane shows as a tree (None: an empty list of its own)."""
    album_chosen = Signal(object)                    # an album chosen here that the list hides
    loaded = Signal(str)                             # a background read finished ("tags", "covers")

    def __init__(self, owner=None, rows=None):
        super().__init__()
        self.owner = owner
        self.albums = []
        self.scope = None                            # (album, kind, key)
        self._quiet = False                          # (a counterpart being highlighted: no echo)
        self.tag_worker = self.cover_worker = None
        self.current = {}                            # file -> its tags as read (the tags that stay)
        self.covers = []                             # the cover strip's choices (covers_find.Choice)
        self.opened_at = None
        self._cover_album = None                     # (the album the cover strip shows)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setObjectName("paneSplit")
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(1)
        self.hsplit = QSplitter(Qt.Horizontal)
        self.hsplit.setObjectName("paneSplit")
        self.hsplit.setChildrenCollapsible(False)
        self.hsplit.setHandleWidth(1)
        self.panes = {}
        # ---- Your files
        p = self.panes["folders"] = Pane("folders", tr("Your files"))
        self.folders = FoldersModel()
        self.folder_view = self._tree(self.folders, tr("Your files"), (110,))
        self.folder_view.setSelectionMode(QAbstractItemView.ExtendedSelection)   # (several: one profile for all)
        self.folder_view.setDragEnabled(True)
        self.folder_view.setDragDropMode(QAbstractItemView.DragOnly)
        self.folder_view.setHeaderHidden(True)           # (the pane's title says what it is)
        p.add(self.folder_view, 1)
        # ---- Albums as they will be: the Overview's list itself
        p = self.panes["albums"] = Pane("albums", tr("Albums as they will be"))
        self.album_model = AlbumsModel(rows)
        self.album_model.join_asked.connect(self.join)
        self.album_view = AlbumsView()
        self.album_view.setObjectName("paneView")
        self.album_view.setModel(self.album_model)
        self.album_view.setAccessibleName(tr("Albums as they will be"))
        self.album_view.setUniformRowHeights(True)
        self.album_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.album_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.album_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.album_view.setFrameShape(QFrame.NoFrame)
        self.album_view.setItemDelegateForColumn(AlbumsModel.NAME, NameDelegate(self.album_view))
        self.album_view.setAcceptDrops(True)
        self.album_view.setDragDropMode(QAbstractItemView.DropOnly)
        self.album_view.setDropIndicatorShown(True)
        h = self.album_view.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(AlbumsModel.NAME, QHeaderView.Stretch)
        h.setSectionResizeMode(AlbumsModel.STATUS, QHeaderView.Interactive)
        h.setSectionResizeMode(AlbumsModel.SCORE, QHeaderView.Fixed)
        h.resizeSection(AlbumsModel.STATUS, 160)
        h.resizeSection(AlbumsModel.SCORE, 56)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSortIndicator(AlbumsModel.STATUS, Qt.AscendingOrder)
        f = style.font("caption")                       # (the column titles as the panes' titles: small capitals)
        f.setPixelSize(11)
        f.setWeight(QFont.Weight(600))
        f.setCapitalization(QFont.AllUppercase)
        h.setFont(f)
        self.album_view.setSortingEnabled(True)
        self.album_view.selectionModel().currentChanged.connect(
            lambda cur, _prev: self.picked(self.album_view, cur))
        p.head.hide()                                    # (its title is the tree's own column header)
        p.add(self.album_view, 1)
        self.release_btn = QToolButton()                 # (Album › Change Release, for the album shown)
        self.release_btn.setProperty("role", "tool")
        self.release_btn.setText(tr("Change Release…"))
        self.release_btn.setIcon(icons.icon("disc-3", "ink"))
        self.release_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.release_btn.setAutoRaise(True)
        self.release_btn.setAccessibleName(tr("Change Release"))
        self.release_btn.setToolTip(tr("Use another release for the selected album (Album › Change Release)"))
        self.release_btn.clicked.connect(self.change_release)
        for k in ("folders", "albums"):
            self.hsplit.addWidget(self.panes[k])
        self.hsplit.setStretchFactor(0, 2)
        self.hsplit.setStretchFactor(1, 3)
        self.vsplit.addWidget(self.hsplit)
        # ---- the inspector: Tags | Why This Release, beside the Cover
        self.bottom = QWidget()
        self.bottom.setObjectName("inspectorPane")
        self.bottom.setAccessibleName(tr("Inspector"))
        bl = QHBoxLayout(self.bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        p = self.panes["tags"] = Pane("tags", tr("Tags"), "", head_height=40)
        p.title_label.hide()
        self.tabs = Segmented([(k, tr(w)) for k, w in INSPECTOR_TABS], tr("Inspector tabs"))
        self.tabs.changed.connect(self.tab_changed)
        p.head_lay.insertWidget(0, self.tabs)
        p.subtitle.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # MOV-5/MOV-6: an album kept where it is for want of evidence says why, and offers its
        # filing (Album › Move into the Library Layout Anyway) over its tags
        self.offer = QFrame()
        self.offer.setObjectName("offerBar")
        ol = QVBoxLayout(self.offer)                     # (the words over the button: a narrow pane cuts neither)
        ol.setContentsMargins(12, 8, 12, 8)
        ol.setSpacing(6)
        self.offer_words = QLabel("")
        self.offer_words.setWordWrap(True)
        self.offer_btn = QToolButton()
        self.offer_btn.setObjectName("offerButton")
        self.offer_btn.setText(tr("Move into Library Anyway"))
        self.offer_btn.setFocusPolicy(Qt.StrongFocus)
        self.offer_btn.clicked.connect(self.file_anyway)
        ol.addWidget(self.offer_words)
        ol.addWidget(self.offer_btn, 0, Qt.AlignLeft)
        self.offer.hide()
        p.add(self.offer)
        self.stack = QStackedWidget()
        p.add(self.stack, 1)
        # Tags
        tags = QWidget()
        tl = QVBoxLayout(tags)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        self.tag_model = TagModel()
        self.tag_model.typed = self.typed
        self.tag_view = QTableView()
        self.tag_view.setObjectName("paneView")
        self.tag_view.setModel(self.tag_model)
        self.tag_view.setAccessibleName(tr("Tag changes: original and new values"))
        self.tag_view.verticalHeader().hide()
        self.tag_view.setShowGrid(False)
        self.tag_view.setAlternatingRowColors(True)
        self.tag_view.setWordWrap(False)
        self.tag_view.setFrameShape(QFrame.NoFrame)
        self.tag_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tag_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tag_view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tag_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tag_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tag_view.customContextMenuRequested.connect(self.tag_menu)
        self.diff = DiffDelegate(self.tag_view)
        for c in (2, 3):
            self.tag_view.setItemDelegateForColumn(c, self.diff)
        left_header(self.tag_view)
        h = self.tag_view.horizontalHeader()
        h.setMinimumSectionSize(24)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 28)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.resizeSection(1, 170)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        tl.addWidget(self.tag_view, 1)
        foot = QWidget()
        foot.setObjectName("paneFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 2, 8, 2)
        fl.setSpacing(4)
        self.tag_note = ElidedLabel("")
        self.tag_note.setProperty("role", "caption")
        fl.addWidget(self.tag_note, 1)
        self.rating = None
        if owner is not None and getattr(getattr(owner, "win", None), "inspector", None) is not None:
            from .account import RatingBox
            from .inspector import RATING_TIP
            self.rating = RatingBox(owner.win.inspector)
            self.rating.setAccessibleName(tr("Rating"))
            self.rating.setToolTip(RATING_TIP)
            self.rating.setVisible(False)
            fl.addWidget(self.rating)
        self.tag_buttons = {}
        from .inspector import BUTTON_KEYS, BUTTON_TIPS, BUTTONS
        fns = {"edit": self.edit_selected, "revert": self.revert_selected, "suggested": self.use_suggested,
               "add": self.add_tag, "remove": self.remove_selected}
        for key, words, icon, _fn in BUTTONS:
            words = tr(words)
            b = QToolButton()
            b.setProperty("role", "tool")
            b.setIcon(icons.icon(icon, "ink"))
            b.setText(words)
            tip = f"{words}: {tr(BUTTON_TIPS[key])}" if key in BUTTON_TIPS else words
            b.setToolTip(f"{tip} ({BUTTON_KEYS[key]})" if key in BUTTON_KEYS else tip)
            b.setAccessibleName(words.rstrip("…"))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.clicked.connect(lambda _=False, f=fns[key]: f())
            fl.addWidget(b)
            self.tag_buttons[key] = b
        self.revert_btn = self.tag_buttons["revert"]
        foot.setFixedHeight(32)
        tl.addWidget(foot)
        self.stack.addWidget(tags)
        # Why This Release: the plain words, then the evidence (the inspector's Evidence tab)
        from .inspector import EvidenceView
        self.evidence = EvidenceView()
        why_body = self.evidence.widget()
        self.why = QLabel("")
        self.why.setObjectName("whyBox")
        self.why.setWordWrap(True)
        self.why.setTextFormat(Qt.RichText)
        self.why.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.why.setAccessibleName(tr("Why this release"))
        why_body.layout().insertWidget(0, self.why)
        why_body.layout().insertSpacing(1, 16)
        self.why_box = self.evidence
        self.stack.addWidget(self.evidence)
        self.tag_view.selectionModel().selectionChanged.connect(lambda *_: self._tag_buttons())
        self.tag_view.installEventFilter(self)              # (Return edits, Delete removes: as the inspector)
        self.tag_view.doubleClicked.connect(self._double_clicked)
        bl.addWidget(p, 1)
        # Cover
        p = self.panes["covers"] = Pane("covers", tr("Cover"), "")
        self.cover_panel = CoverPanel(self)
        self.cover_panel.setObjectName("coverPane")
        cl = QVBoxLayout(self.cover_panel)
        cl.setContentsMargins(16, 4, 16, 10)
        cl.setSpacing(8)
        self.cover_big = FitPicture(COVER_SIDE)
        self.cover_big.setObjectName("coverBig")
        self.cover_big.setAccessibleName(tr("Front cover"))
        cl.addWidget(self.cover_big, 1)
        self.cover_caption = ElidedLabel("")
        self.cover_caption.setProperty("role", "caption")
        self.cover_caption.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        cl.addWidget(self.cover_caption)
        self.cover_strip = CoverStrip()
        self.cover_strip.itemDoubleClicked.connect(lambda _it: self.cover_action("use"))
        self.cover_strip.currentItemChanged.connect(lambda *_: (self._cover_buttons(), self.show_big()))
        cl.addWidget(self.cover_strip)
        self.cover_btn = QToolButton()
        self.cover_btn.setObjectName("coverChoose")
        self.cover_btn.setText(tr("Choose Cover…"))
        self.cover_btn.setAccessibleName(tr("Choose Cover"))
        self.cover_btn.setToolTip(tr("Use the picture selected, find covers online, choose a file or a link, or "
                                     "generate one"))
        self.cover_btn.setPopupMode(QToolButton.InstantPopup)
        self.cover_btn.setFocusPolicy(Qt.StrongFocus)
        menu = QMenu(self.cover_btn)
        self.cover_buttons = {}
        for key, words, tip in (
                ("use", tr("Use as Front Cover"), tr("Use the selected picture as the album's front cover")),
                ("find", tr("Find Online…"), tr("Every cover the cover sources find (Discogs, Cover Art "
                                                "Archive …), to pick one by eye")),
                ("file", tr("Choose a File…"), tr("Any image you have")),
                ("url", tr("Use Image Link…"), tr("An image from the web as the album's new cover")),
                ("make", tr("Generate…"), tr("Clean up your scan or generate a cover with your image service "
                                             "(Make Covers)")),
                ("clear", tr("Use Suggested Cover"), tr("Forget your cover: use the one the preview found"))):
            act = menu.addAction(words)
            act.setStatusTip(tip)
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, k=key: self.cover_action(k))
            self.cover_buttons[key] = act
            if key in ("use", "make"):
                menu.addSeparator()
        self.cover_btn.setMenu(menu)
        cl.addWidget(self.cover_btn, 0, Qt.AlignHCenter)
        self.cover_note = ElidedLabel("")
        self.cover_note.setProperty("role", "caption")
        self.cover_note.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        cl.addWidget(self.cover_note)
        p.add(self.cover_panel, 1)
        p.setFixedWidth(COVER_PANE)
        bl.addWidget(p)
        self.cover_pane = p
        self.vsplit.addWidget(self.bottom)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 3)
        lay.addWidget(self.vsplit)
        # ---- remembered per user
        prefs = self._prefs()
        sizes = prefs.get("sizes")
        self.hsplit.setSizes(sizes[:2] if isinstance(sizes, list) and len(sizes) >= 2 else [430, 650])
        vs = prefs.get("vsizes")
        self.vsplit.setSizes(vs if isinstance(vs, list) and len(vs) == 2 else [440, 330])
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save_sizes)
        self.hsplit.splitterMoved.connect(lambda *_: self._save_timer.start())
        self.vsplit.splitterMoved.connect(lambda *_: self._save_timer.start())
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh_changed)
        self._changed = {}                               # id(album) -> album, refreshed together
        # (the reads stop when the panes go: the handler holds the workers' list, not the panes)
        self._workers = []
        workers = self._workers
        self.destroyed.connect(lambda *_: [setattr(w, "cancelled", True) for w in workers])
        self.show_scope()

    # ---- settings ---------------------------------------------------------------------------
    @staticmethod
    def _prefs() -> dict:
        return clean_prefs(_gui_prefs().get(PREF))

    def _save(self, **kw):
        p = self._prefs()
        p.update(kw)
        _set_gui_pref(PREF, p)

    def save_sizes(self):
        """The panes' widths and the inspector's height, as you left them."""
        p = self._prefs()
        sizes = self.hsplit.sizes()
        if len(sizes) == 2 and all(s > 0 for s in sizes):
            p["sizes"] = sizes + [1]                         # (a third, kept for an older version)
        vs = self.vsplit.sizes()
        if self.bottom.isVisible() and all(s > 0 for s in vs):
            p["vsizes"] = vs
        _set_gui_pref(PREF, p)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        want = COVER_PANE if self.width() >= 1000 else 220     # (a narrow window: the tags keep their room)
        if self.cover_pane.width() != want:
            self.cover_pane.setFixedWidth(want)

    def set_inspector(self, on):
        """The inspector pane (the toolbar's Inspector button) shown or hidden."""
        self.bottom.setVisible(bool(on))
        if on and self.scope and self.scope[0] is not self._cover_album:
            self.load_covers()

    def inspector_shown(self) -> bool:
        return not self.bottom.isHidden()

    def tab_changed(self, key):
        self.stack.setCurrentIndex(0 if key == "tags" else 1)
        self._save(tab=key)

    def show_tab(self, key):
        """Tags, or Why This Release ("why"; the inspector's Evidence); Artwork: the cover, beside."""
        self.tabs.set_current("why" if key in ("why", "Evidence") else "tags")

    def retint(self):
        from .inspector import BUTTONS
        for key, _w, icon, _f in BUTTONS:
            self.tag_buttons[key].setIcon(icons.icon(icon, "ink"))
        self.release_btn.setIcon(icons.icon("disc-3", "ink"))
        self.album_model.forget_looks()
        self.album_view.viewport().update()
        self.folder_view.viewport().update()

    # ---- the trees --------------------------------------------------------------------------
    def _tree(self, model, name, widths=(120, 120)):
        v = QTreeView()
        v.setObjectName("paneView")
        v.setModel(model)
        v.setAccessibleName(name)
        v.setUniformRowHeights(True)                    # (thousands of rows laid out without measuring each)
        v.setAlternatingRowColors(False)
        v.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.setSelectionMode(QAbstractItemView.SingleSelection)
        v.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.setTextElideMode(Qt.ElideRight)
        v.setFrameShape(QFrame.NoFrame)
        v.header().setStretchLastSection(False)
        v.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in zip(range(1, model.columnCount()), widths):
            v.header().setSectionResizeMode(c, QHeaderView.Interactive)
            v.header().resizeSection(c, w)
        v.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.selectionModel().currentChanged.connect(lambda cur, _prev, v=v: self.picked(v, cur))
        return v

    def set_albums(self, albums):
        """The plan's albums (the list's own entries, in its order): Your files made again (the
        albums pane follows the list by itself)."""
        t0 = time.perf_counter()
        self.albums = albums if albums is not None else []
        forget()
        keep = self.scope
        self._quiet = True
        try:
            self.folders.set_albums(self.albums)
        finally:
            self._quiet = False
        self.opened_at = time.perf_counter() - t0
        roots = self.folders.rowCount()
        n_files = sum(len(a.get("files") or []) for a in self.albums)
        self.panes["folders"].subtitle.setText(
            " · ".join(x for x in (plural(roots, tr("folder"), tr("folders")) if roots else "",
                                   plural(n_files, tr("file"), tr("files")) if n_files else "") if x))
        # (the albums pane is the list: its selection is its own, kept by it - never set again here)
        if keep and any(a is keep[0] for a in self.albums):
            self.show_scope(*keep, source=self.album_view)
        else:
            self.show_scope(None, source=self.album_view)

    def album_changed(self, album):
        """ALBUM's entry changed (your edit, its look-up): its rows made again, when next shown."""
        self._changed[id(album)] = album
        self._refresh_timer.start()

    def _refresh_changed(self):
        changed, self._changed = self._changed, {}
        if not changed:
            return
        ids = set(changed)
        self._quiet = True
        try:
            for a in changed.values():
                forget(a)
            lo = hi = None
            for n in self.folders.top.kids:
                if any(id(a) in ids for a in n.data["albums"]):
                    self.folders.reset_kids(n)
                    lo = n.row if lo is None else min(lo, n.row)
                    hi = n.row if hi is None else max(hi, n.row)
            if lo is not None:
                cells_changed(self.folders, lo, hi, 2)
        finally:
            self._quiet = False
        if self.scope and id(self.scope[0]) in ids:
            self.show_scope(*self.scope, source=self.album_view)

    # ---- selection, kept in step --------------------------------------------------------------
    def show_album(self, album):
        """The list chose ALBUM (None: none): shown here, a disc, track or file of it already
        chosen kept."""
        if album is None:
            if self.scope is not None:
                self.show_scope(None)
            return
        if self.scope and self.scope[0] is album:
            return
        self.show_scope(album, "album", None, source=self.album_view)

    def picked(self, view, idx):
        if self._quiet or not idx.isValid() or idx.model() is not view.model():
            return
        n = idx.internalPointer()
        if view is self.folder_view:
            if n.kind == "file":
                scope = (n.album, "file", n.data["fe"]["src"])
            else:
                albums = n.data["albums"]
                a = n.album or (albums[0] if len(albums) == 1 else None)
                if a is None:
                    scope = (albums[0], "folder", n.data["path"])
                elif n.kind == "root" and album_root(a) == n.data["path"]:
                    scope = (a, "album", None)
                else:
                    scope = (a, "folder", n.data["path"])
        else:
            if n.kind == "album":
                scope = (n.album, "album", None)
            elif n.kind == "disc":
                scope = (n.album, "disc", n.data["key"]) if not n.data.get("extra") else \
                    (n.album, "extra", None)
            else:
                fe = n.data["fe"]
                scope = (n.album, "track", fe["src"]) if fe else (n.album, "missing", n.data["number"])
        self.show_scope(*scope, source=view)

    def show_scope(self, album=None, kind="album", key=None, source=None):
        self.scope = (album, kind, key) if album is not None else None
        self._quiet = True
        try:
            if source is not self.folder_view:
                self._highlight(self.folder_view, self._folder_node(album, kind, key))
            if source is not self.album_view and album is not None:
                node = self._album_node(album, kind, key)
                if node is None:                             # (hidden by the search or the filter)
                    self.album_chosen.emit(album)
                    node = self._album_node(album, kind, key)
                if node is not None:
                    self._show_in_list(node)
        finally:
            self._quiet = False
        self.release_btn.setEnabled(album is not None)
        self.show_offer(album)
        self.show_why(album)
        self.read_tags()
        self.fill_tags()
        self.show_rating()
        if album is not self._cover_album or album is None:
            self.load_covers()

    def _show_in_list(self, node):
        """The albums pane's row for NODE highlighted only to show it (the Overview: show_row)."""
        owner = self.owner
        if owner is not None:
            owner._showing_row = True
        try:
            self._highlight(self.album_view, node)
        finally:
            if owner is not None:
                owner._showing_row = False

    def _highlight(self, view, node):
        model = view.model()
        if node is None:
            view.selectionModel().clear()
            return
        idx = model.index_of(node)
        p = idx.parent()
        while p.isValid():
            view.expand(p)
            p = p.parent()
        view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        view.scrollTo(idx)

    def _album_node(self, album, kind, key):
        if album is None:
            return None
        top = self.album_model.node_of(album)
        if top is None or kind == "album":
            return top
        discs = self.album_model.kids(top)
        if kind == "disc":
            return next((d for d in discs if d.data["key"] == key), top)
        if kind == "extra":
            return next((d for d in discs if d.data.get("extra")), top)
        if kind in ("track", "file"):
            for d in discs:
                for t in self.album_model.kids(d):
                    if t.data["fe"] and t.data["fe"].get("src") == key:
                        return t
            return top
        if kind == "folder":                                # (the disc that folder holds)
            for d in discs:
                if key in d.data["folders"]:
                    return d
        return top

    def _folder_node(self, album, kind, key):
        if album is None:
            return None
        root = next((n for n in self.folders.top.kids if any(a is album for a in n.data["albums"])), None)
        if root is None:
            return None
        target = None
        if kind in ("track", "file"):
            target = key
        elif kind == "folder":
            target = key
        elif kind == "disc":
            files = _scope_files(album, "disc", key)
            folders = {str(Path(fe["src"]).parent) for fe in files}
            target = next(iter(folders)) if len(folders) == 1 else None
        if not target or target == root.data["path"]:
            return root
        node = root
        while True:
            nxt = None
            for k in self.folders.kids(node):
                p = k.data["fe"]["src"] if k.kind == "file" else k.data["path"]
                if p == target or (k.kind != "file" and target.startswith(p.rstrip(os.sep) + os.sep)):
                    nxt = k
                    break
            if nxt is None:
                return node
            if (nxt.data["fe"]["src"] if nxt.kind == "file" else nxt.data["path"]) == target:
                return nxt
            node = nxt

    def select(self, kind, key=None, album=None):
        """Choose a part of ALBUM (the one shown when None) here, as a click in a pane does."""
        album = album or (self.scope[0] if self.scope else None)
        if album is not None:
            self.show_scope(album, kind, key)

    def folder_albums(self) -> list:
        """The albums the rows selected in Your files hold (a folder: each of its albums; a file:
        its album), once each, in order."""
        out, seen = [], set()
        for idx in self.folder_view.selectionModel().selectedRows():
            n = idx.internalPointer()
            for a in ([n.album] if n.kind == "file" else n.data["albums"]):
                if a is not None and id(a) not in seen:
                    seen.add(id(a))
                    out.append(a)
        return out

    # ---- why ----------------------------------------------------------------------------------
    def show_why(self, album):
        if album is None:
            self.why.setText("")
            self.evidence.setVisible(self.stack.currentWidget() is self.evidence)
            return
        title, lines = why_lines(album)
        colour = {"✓": style.color("identified"), "≠": style.color("attention"), "→": style.color("muted"),
                  "·": style.color("muted")}
        body = "".join(f"<tr><td style='color:{colour[m]};font-weight:600;padding-right:8px'>{m}</td>"
                       f"<td>{html.escape(w)}</td></tr>" for m, w in lines)
        self.why.setText(f"<b>{html.escape(title)}</b><table cellspacing='0'>{body}</table>" if lines else
                         f"<b>{html.escape(title)}</b><br>{html.escape(tr('Nothing recorded for this album.'))}")
        self.why.setAccessibleName(title)
        self.evidence.show_album(album, getattr(self.owner, "plan", None))

    # ---- tag changes --------------------------------------------------------------------------
    def scope_files(self) -> list:
        if not self.scope:
            return []
        album, kind, key = self.scope
        if kind == "extra":
            return []
        return _scope_files(album, kind, key)

    def scope_words(self) -> tuple:
        if not self.scope:
            return tr("Nothing selected"), ""
        album, kind, key = self.scope
        files = self.scope_files()
        n = plural(len(files), tr("file"), tr("files"))
        if kind == "album":
            folders = {str(Path(fe["src"]).parent) for fe in files}
            discs = {disc_of(fe) for fe in files}
            return album_title(album), " · ".join(x for x in (
                plural(len(discs), tr("disc"), tr("discs")) if len(discs) > 1 else "", n,
                tr("from {0} folders").format(len(folders)) if len(folders) > 1 else "") if x)
        if kind == "disc":
            folders = sorted({Path(fe["src"]).parent.name for fe in files})
            return disc_label(key), n + (tr(" · from “{0}”").format(folders[0]) if len(folders) == 1 else "")
        if kind in ("track", "file"):
            fe = files[0] if files else {}
            d = disc_label(disc_of(fe)) if fe else ""
            no = track_of(fe) if fe else None
            return (f"{d} · " + tr("track {0}").format(no)) if no else Path(key).name, Path(key).name
        if kind == "folder":
            return Path(key).name, n
        if kind == "missing":
            return tr("Track {0}").format(key), tr("Not in your folders: nothing to tag")
        return tr("Not on the release"), tr("These files are not tagged with the album")

    def read_tags(self):
        """The selected files' tags as they are now (for the rows that stay as they are), read on a
        worker; a newer selection cancels the older read."""
        if self.tag_worker is not None:
            self.tag_worker.cancelled = True
        self.tag_worker = None
        files = [fe["src"] for fe in self.scope_files()][:READ_MOST]
        # (what was read of these files stays shown until the new read arrives: an edit or a
        # reselection never empties the rows in between)
        self.current = {f: self.current[f] for f in files if f in (self.current or {})}
        if not files or self.owner is None:
            return
        scope = self.scope

        def work(w):
            import tagio
            out = {}
            for f in files:
                if w.cancelled:
                    return None
                try:
                    out[f] = tagio.read(f)
                except Exception:
                    continue
            return out

        def done(got):
            if w_ref[0] is not self.tag_worker or got is None or self.scope != scope:
                return                                     # (cancelled, or another selection now)
            self.tag_worker = None
            self.current = got
            self.fill_tags()
            self.loaded.emit("tags")

        w_ref = [None]
        w_ref[0] = self.tag_worker = self._started(start(self, work, done, failed=lambda _m: None))

    def editable(self) -> bool:
        if not self.scope or self.owner is None:
            return False
        insp = self._insp()
        return bool(insp is not None and self.scope_files() and insp.can_edit(self.scope[0]))

    def fill_tags(self):
        title, sub = self.scope_words()
        p = self.panes["tags"]
        say(p.subtitle, tr("Selection"), f"{title} · {sub}" if sub else title)
        files = self.scope_files()
        # (every tag, always: the album's own first, then the tracks'; what changes is marked)
        rows = tag_rows(files, self.current) if files else []
        self.tag_model.set_rows(rows, self.editable())
        n = sum(1 for r in rows if r["mark"] != "=")
        if not self.scope:
            note = tr("Select a folder, album, disc or track to see its tag changes.")
        elif not files:
            note = sub
        elif not rows:
            note = tr("No tags in this selection.") if self.tag_worker is None else tr("Reading the tags…")
        else:
            note = tr("{0} in {1}.").format(plural(n, tr("change"), tr("changes")),
                                            plural(len(files), tr("file"), tr("files")))
            if len(files) > READ_MOST:
                note += " " + tr("The tags that stay are read from the first {0} files.").format(READ_MOST)
            if self.editable() and self.plain():
                note += " " + tr("Double-click a new value to edit it; nothing is written until you save "
                                 "(File › Save Tags). Nothing is looked up.")
            elif self.editable():
                note += " " + tr("Double-click a new value to edit it; nothing is written until you apply.")
        self.tag_note.setText(note)
        self._tag_buttons()

    def show_rating(self):
        """The rating stars: for the file shown (a track), or every file of the album."""
        if self.rating is None:
            return
        album, kind, key = self.scope if self.scope else (None, None, None)
        files = (album or {}).get("files") or []
        on = bool(files) and kind in ("album", "track", "file")
        self.rating.setVisible(on)
        if on:
            self.rating.show_album(album, key if kind in ("track", "file") else None)

    def selected_tag(self):
        idx = self.tag_view.currentIndex()
        if not idx.isValid() or not self.tag_view.selectionModel().hasSelection():
            return None
        return self.tag_model.rows[idx.row()] if 0 <= idx.row() < len(self.tag_model.rows) else None

    def _tag_buttons(self):
        r = self.selected_tag()
        on = self.editable()
        ok = bool(r) and self.tag_model.can_edit(r)
        self.tag_buttons["revert"].setEnabled(ok and (r["mark"] != "=" or r["mine"]))
        self.tag_buttons["edit"].setEnabled(ok)
        self.tag_buttons["remove"].setEnabled(ok and (r["mixed_after"] or bool(r["after_vals"])))
        self.tag_buttons["suggested"].setEnabled(ok and r["mine"])
        self.tag_buttons["add"].setEnabled(on)
        win = getattr(self.owner, "win", None)
        if win is not None and hasattr(win, "update_tag_actions"):
            win.update_tag_actions()                         # (the Album > Tag commands follow)

    def _edit(self, field_, value):
        """Your value for FIELD on the selection, through the inspector's edits."""
        insp = self._insp()
        if insp is None or not self.editable():
            return False
        album, kind, _key = self.scope
        files = [fe["src"] for fe in self.scope_files()]
        if kind == "album":
            insp.set_tag(field_, value, None, album=album)
        elif len(files) == 1:
            insp.set_tag(field_, value, files[0], album=album)
        else:
            insp.set_tag_files(field_, value, files, album=album)
        return True

    def typed(self, row, text):
        """A value typed in a New cell: only for a row every file shares with one value at most
        (TagModel.inline). Left empty, or as it was, nothing changes: a tag is removed only with
        Remove Tag."""
        text = (text or "").strip()
        if not self.tag_model.inline(row) or not text or text == tr(NONE):
            return
        if text == _join(row["after_vals"] or []):
            return
        self._edit(row["field"], [text])

    def _differ_ok(self, row, what) -> bool:
        """Files whose values differ get one value only when you say so."""
        if not row["mixed_after"]:
            return True
        from . import sheets
        return sheets.confirm(self, tr("{what} {tag} on all {n} files?").format(what=what, tag=row["name"], n=row["n"]),
                              tr("The selected files have different values for {tag}; each would then have the "
                                 "same.").format(tag=row["name"]),
                              tr("Nothing is written until you save; Edit › Undo Edit takes it back.") if self.plain()
                              else tr("Nothing is written until you apply; Edit › Undo Edit takes it back."),
                              what, destructive=True)

    def plain(self) -> bool:
        """EDT-2: the selection is of an album not looked up - its edits are saved with Save Tags."""
        return bool(self.scope) and (self.scope[0] or {}).get("status") == planmod.LISTED

    def until_words(self) -> str:
        return tr(" Nothing is written until you save (Save Tags).") if self.plain() else \
            tr(" Nothing is written until you apply the preview.")

    def edit_key(self):
        """F2 or Return on a row: its New value typed in place when it is one value, else Edit
        Value… (several values, or files that differ)."""
        r = self.selected_tag()
        if r is None or not self.tag_model.can_edit(r):
            return
        if self.tag_model.inline(r):
            idx = self.tag_model.index(self.tag_model.rows.index(r), 3)
            self.tag_view.setFocus()
            self.tag_view.setCurrentIndex(idx)
            self.tag_view.edit(idx)
        else:
            self.edit_selected()

    def edit_selected(self):
        """Edit Value…: the inspector's sheet, one value a line (several values, or files that
        differ - asked first)."""
        r = self.selected_tag()
        if not r or not self.tag_model.can_edit(r):
            return
        if not self._differ_ok(r, tr("Set")):
            return
        from . import inspector
        files = self.scope_files()
        scope = tr("For {0}.").format(plural(len(files), tr("file"), tr("files"))) + self.until_words()
        got = inspector.tag_sheet(self, tr("Edit {0}").format(r["name"]), scope, name=r["field"],
                                  values=r["after_vals"] or [])
        # (an empty sheet removes nothing: Remove Tag does; OK with the values unchanged is no edit -
        # it would pin them as yours, kept when the release changes later)
        if got is not None and got[1] and list(got[1]) != list(r["after_vals"] or []):
            self._edit(r["field"], got[1])

    def add_tag(self):
        """Add Tag…: a tag's name and values for the selection (the inspector's sheet)."""
        if not self.editable():
            return
        from . import inspector, sheets
        files = self.scope_files()
        scope = tr("For {0}.").format(plural(len(files), tr("file"), tr("files"))) + self.until_words()
        got = inspector.tag_sheet(self, tr("Add a Tag"), scope, ask_name=True, action=tr("Add Tag"))
        if got is None:
            return
        name = inspector.internal_name(got[0])
        why = inspector.bad_name(name) or ("" if got[1] else tr("Type at least one value."))
        if why:
            sheets.warning(self, tr("This tag cannot be added."), why)
            return
        self._edit(name, got[1])

    def remove_selected(self):
        r = self.selected_tag()
        if not r or not self.tag_model.can_edit(r) or not (r["mixed_after"] or r["after_vals"]):
            return
        if self._differ_ok(r, tr("Remove")):
            self._edit(r["field"], None)

    def use_suggested(self):
        """Use Suggested Value: your edit of the selected tag forgotten for the selection."""
        r = self.selected_tag()
        insp = self._insp()
        if not r or not r["mine"] or insp is None or not self.editable():
            return
        album, kind, _key = self.scope
        files = [fe["src"] for fe in self.scope_files()]
        if kind == "album":
            insp.clear_tag(r["field"], None, album=album)
        else:
            insp.clear_tag_files(r["field"], files, album=album)

    def _double_clicked(self, idx):
        if idx.isValid() and 0 <= idx.row() < len(self.tag_model.rows):
            r = self.tag_model.rows[idx.row()]
            if not (idx.column() == 3 and self.tag_model.inline(r)):
                self.edit_selected()

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.tag_view and e.type() == QEvent.KeyPress and \
                self.tag_view.state() != QAbstractItemView.EditingState and not e.modifiers():
            if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
                self.edit_key()
                return True
            if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                self.remove_selected()
                return True
        return super().eventFilter(obj, e)

    def revert_selected(self):
        import plan as planmod
        r = self.selected_tag()
        if r and self.tag_model.can_edit(r):
            self._edit(r["field"], dict(planmod.KEEP))

    def tag_menu(self, pos):
        """The Album > Tag commands (with their keys) on the table's row."""
        win = getattr(self.owner, "win", None)
        if win is None or "edit_tag" not in getattr(win, "commands", {}):
            return
        from .inspector import TAG_COMMANDS
        win.update_tag_actions()
        self._menu = popup_menu(self, self.tag_view, pos, [win.act(k) if k else None for k in TAG_COMMANDS])

    # ---- covers -------------------------------------------------------------------------------
    def load_covers(self):
        """The album's own pictures and its new front, read on a worker (a newer album cancels it)."""
        if self.cover_worker is not None:
            self.cover_worker.cancelled = True
        self.cover_worker = None
        self.cover_strip.clear()
        self.covers = []
        album = self.scope[0] if self.scope else None
        self._cover_album = album
        self.panes["covers"].subtitle.setText("")
        self.show_big()
        self._cover_buttons()
        if album is None or self.owner is None:
            self.cover_note.setText("" if album is None else "")
            return
        folder = album.get("source_dir") or ""
        files = [fe["src"] for fe in album.get("files") or []]
        art = dict(album.get("artwork") or {})
        self.cover_note.setText(tr("Reading the album's pictures…"))

        def work(w):
            import covers_find
            out = []
            front = art.get("front")
            if front and os.path.isfile(front):
                try:
                    data = Path(front).read_bytes()
                    out.append(covers_find.Choice("new", str(art.get("source") or ""), int(art.get("width") or 0),
                                                  int(art.get("height") or 0), covers_find.thumbnail(data), data,
                                                  extra={"front": True, "path": front}))
                except OSError:
                    pass
            if w.cancelled:
                return None
            out += covers_find.own(folder, files[:1]) if folder else []
            return None if w.cancelled else out

        def done(got):
            if ref[0] is not self.cover_worker or got is None or not self.scope or self.scope[0] is not album:
                return
            self.cover_worker = None
            self.show_covers(got, art)
            self.loaded.emit("covers")

        def failed(msg):
            if ref[0] is self.cover_worker:
                self.cover_worker = None
                self.cover_note.setText(tr("The pictures could not be read: {0}").format(msg.split("\n\n")[0]))

        ref = [None]
        ref[0] = self.cover_worker = self._started(start(self, work, done, failed=failed))

    def show_covers(self, choices, art):
        import plan as planmod
        self.covers = list(choices)
        for i, c in enumerate(self.covers):
            size = f"{c.width} × {c.height}" if c.width else ""
            if c.kind == "new":
                src = str(c.source or "")
                words = tr("New front cover") + "\n" + (tr("your image") if art.get("by") == planmod.BY_YOU else
                                                        src.replace("caa", "Cover Art Archive")
                                                        .replace("discogs", "Discogs"))
            elif c.extra.get("file"):
                words = tr("In the tracks") + "\n" + size
            else:
                words = (tr("Your scan") if not c.extra.get("front") else tr("Your cover")) + "\n" + \
                    Path(c.source).name + (f" · {size}" if size else "")
            it = QListWidgetItem(words)
            if c.thumb:
                px = QPixmap()
                px.loadFromData(c.thumb)
                it.setIcon(QIcon(px))
            else:
                it.setIcon(_placeholder("?"))
            it.setData(R_USER, i)
            it.setToolTip(words.replace("\n", " · "))
            it.setData(R_ACCESSIBLETEXT, words.replace("\n", ", ") + (f", {size}" if size else ""))
            self.cover_strip.addItem(it)
        n_own = sum(1 for c in self.covers if c.kind == "own")
        has_new = any(c.kind == "new" for c in self.covers)
        self.panes["covers"].subtitle.setText(tr("new cover") if has_new else
                                              tr("from your scans") if n_own else "")
        self.cover_note.setText(
            (tr("The new front cover first, then your own pictures.") if has_new else
             tr("No new cover: the album keeps its own.")) + " " +
            (tr("{0} of your own.").format(n_own) if n_own else tr("No pictures of your own.")))
        if self.cover_strip.count():
            self.cover_strip.setCurrentRow(0)
        self.show_big()
        self._cover_buttons()

    def show_big(self):
        """The picture selected in the strip (the new front first), large, with its words."""
        c = self.chosen_cover()
        if c is None or not c.thumb:
            self.cover_big.setPixmap(QPixmap())
            self.cover_big.setText(tr("No cover") if self.scope and not self.cover_worker else "")
            self.cover_caption.setText("")
            self.cover_big.setAccessibleName(tr("No cover"))
            return
        px = QPixmap()
        px.loadFromData(c.thumb)
        self.cover_big.setPixmap(px)
        it = self.cover_strip.currentItem()
        words = (it.text() if it is not None else "").replace("\n", " · ")
        size = f"{c.width} × {c.height}" if c.width else ""
        caption = " · ".join(x for x in (words.split(" · ")[0], size,
                                         Path(str(c.extra.get("path") or c.source or "")).name) if x)
        self.cover_caption.setText(caption)
        self.cover_big.setAccessibleName(caption)

    def chosen_cover(self):
        it = self.cover_strip.currentItem()
        return self.covers[it.data(R_USER)] if it is not None and 0 <= it.data(R_USER) < len(self.covers) \
            else None

    def _insp(self):
        return getattr(getattr(self.owner, "win", None), "inspector", None)

    def cover_editable(self) -> bool:
        insp = self._insp()
        album = self.scope[0] if self.scope else None
        return bool(insp is not None and album is not None and insp.can_edit(album))

    def _cover_buttons(self):
        import plan as planmod
        on = self.cover_editable()
        c = self.chosen_cover()
        album = self.scope[0] if self.scope else None
        self.cover_buttons["use"].setEnabled(on and c is not None and c.kind != "new")
        for k in ("find", "file", "url", "make"):
            self.cover_buttons[k].setEnabled(on)
        self.cover_buttons["clear"].setEnabled(on and ((album or {}).get("artwork") or {}).get("by") == planmod.BY_YOU)
        self.cover_btn.setEnabled(on)

    def cover_action(self, key):
        """The cover's commands: each the inspector's own (its Artwork's Find Covers…, Choose
        Image…, Use Image Link…, Use Suggested Cover, the Find Covers sheet's Make Covers…, and a
        picture used as Choose Image uses one)."""
        insp = self._insp()
        album = self.scope[0] if self.scope else None
        if insp is None or album is None or not insp.can_edit(album):
            return
        if insp.album is not album:
            insp.show_album(album, getattr(self.owner, "plan", None))
        if key == "use":
            c = self.chosen_cover()
            if c is None or c.kind == "new":
                return
            insp._fetch(c.source, c.full, f"chosen:{c.kind}:{c.source}")
        elif key == "find":
            insp.art.find_covers()
        elif key == "file":
            insp.art.choose_file()
        elif key == "url":
            insp.art.ask_url()
        elif key == "clear":
            insp.clear_cover(album)
        elif key == "make":
            from . import sheets
            from .cover_chooser import CoverChooser
            dlg = CoverChooser(insp, album)                # (the Find Covers sheet, its Make Covers… asked)
            self._chooser = dlg
            QTimer.singleShot(0, dlg, dlg.make_ai)
            sheets.run(dlg, "covers")

    # ---- the list's commands ----------------------------------------------------------------
    def show_offer(self, album):
        """MOV-5/MOV-6: why ALBUM is not filed (its score, no release proven) and the button that
        asks for it; hidden for any other album."""
        from .models import not_filed_reason, release_offered, unresolved_reason
        words = not_filed_reason(album) if album is not None else ""
        if not words and album is not None and release_offered(album):      # (MOV-6: Unresolved)
            ov = self.owner
            words = unresolved_reason(album, ov.unresolved_action(album) if ov is not None and
                                      hasattr(ov, "unresolved_action") else "")
        self.offer.setVisible(bool(words))
        if not words:
            return
        copy = (album.get("below_floor") or {}).get("op") == "copy"
        self.offer_words.setText(words)
        self.offer_btn.setText(tr("Copy into Library Anyway") if copy else tr("Move into Library Anyway"))
        self.offer_btn.setAccessibleName(self.offer_btn.text())
        self.offer_btn.setEnabled(bool(self.owner is not None and hasattr(self.owner, "may_file_anyway")
                                       and self.owner.may_file_anyway(album)))

    def file_anyway(self):
        album = self.scope[0] if self.scope else None
        if album is not None and self.owner is not None and hasattr(self.owner, "file_anyway_for"):
            self.owner.file_anyway_for([album])

    def change_release(self):
        album = self.scope[0] if self.scope else None
        if album is None or self.owner is None:
            return
        if hasattr(self.owner, "choose_release"):
            self.owner.choose_release()

    def join(self, album, paths):
        """Folders dropped on an album: joined by hand in Match Files Manually (TagMender's own
        joining of disc folders is the planner's, DSC-10); nothing is matched here."""
        if self.owner is None:
            return
        self.select("album", None, album)
        from . import manual_match
        win = getattr(self.owner, "win", None)
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(tr("To add {0} to this album, match its files to the album's tracks in "
                                           "Match Files Manually.").format(", ".join(Path(p).name for p in paths)),
                                        10000)
        manual_match.open_manual_match(self.owner, album)

    # ---- shutting down ------------------------------------------------------------------------
    def _started(self, w):
        self._workers[:] = [x for x in self._workers if not getattr(x, "cancelled", False)][-8:] + [w]
        return w

    def stop(self):
        """Every read of the panes cancelled (the panes are going)."""
        for w in self._workers:
            w.cancelled = True
        self.tag_worker = self.cover_worker = None
