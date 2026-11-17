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
                             style's diff_* tokens); Folder and File name rows; Changes only
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
widths and Changes only are remembered per user (gui.json "preview_panes")."""
from __future__ import annotations

import difflib
import html
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import (QAbstractItemModel, QAbstractTableModel, QItemSelectionModel, QMimeData, QModelIndex,
                            QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QListView,
                               QListWidget, QListWidgetItem, QScrollArea, QSizePolicy, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableView, QToolButton, QTreeView,
                               QVBoxLayout, QWidget)

from . import icons, style
from .common import _gui_prefs, _set_gui_pref, album_words, label_for, left_header, plural, reasons, say
from .i18n import N_, tr
from .models import (R_ACCESSIBLETEXT, R_DECORATION, R_DISPLAY, R_EDIT, R_FONT, R_FOREGROUND, R_TEXTALIGNMENT,
                     R_TOOLTIP, R_USER)
from .tagnames import display as tag_name
from .work import start

PREF = "preview_panes"                  # gui.json: {"collapsed": {pane: bool}, "sizes": [..], "vsizes": [..],
#                                         "changes_only": bool}
PANES = ("folders", "albums", "tags", "covers")
STRIP = 34                              # a folded side pane: a strip this wide
SIDE_MIN = 100                          # an open side pane's narrowest (960 px wide, the inspector open: three fit)
WHY_MOST = 112                          # "Why this release" at most this tall (it scrolls)
COVER_ROW = 124                         # the cover strip's height
NONE = N_("(none)")
FOLDER, FILENAME = "~folder", "~filename"          # the path rows of the tag pane (never tag keys)
MIME = "application/x-tagmender-folders"           # folders dragged from Your folders (one path a line)
READ_MOST = 200                         # the files whose current tags are read for one selection
CHANGE = ("added", "changed", "removed")
MARK = {"added": "+", "changed": "→", "removed": "−"}
MARK_WORDS = {"+": N_("Added"), "→": N_("Changed"), "−": N_("Removed"), "=": N_("Kept")}
_LAYER = re.compile(r"\((SACD(?: Stereo| Multichannel)?)\)\s*$", re.I)
_DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd)\s*[-_.]?\s*0*(\d+)\b", re.I)


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
    import plan as planmod
    return a.get("status") in ("unresolved", "failed", "conflict") or (a.get("status") == "ready" and planmod.held(a))


def album_discs(a) -> list:
    """The album as it will be: [{"key", "label", "folders", "tracks", "extra"}], discs in order,
    then "Not on the release" (files the release has no track for, files left out by a rule,
    files found with the album that are not of it). Each track {"number", "title", "fe" (None:
    the release's track is not in your folders), "missing", "note"}."""
    files = a.get("files") or []
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


def why_lines(a) -> tuple:
    """("Why this release" or another title, [(mark, words)]): ✓ what agreed, ≠ what differs,
    → what to do."""
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
    rows.sort(key=lambda r: (r["mark"] == "=", r.get("path", False), not r["mine"], r["name"].lower()))
    return rows


def _row(field_, name, entries) -> dict:
    befores = {tuple(b) for b, _a, _m, _y in entries}
    afters = {tuple(a) for _b, a, _m, _y in entries}
    marks = {m for _b, _a, m, _y in entries}
    n = len(entries)
    mark = "=" if marks == {"="} else next(iter(marks - {"="})) if len(marks - {"="}) == 1 else "→"
    differ = tr("different in {0} files").format(n)
    bv = list(next(iter(befores))) if len(befores) == 1 else None
    av = list(next(iter(afters))) if len(afters) == 1 else None
    return {"field": field_, "name": name, "mark": mark, "n": n, "path": False,
            "mine": any(y for _b, _a, _m, y in entries),
            "before_vals": bv, "after_vals": av, "mixed_before": bv is None, "mixed_after": av is None,
            "before": (_join(bv) or tr(NONE)) if bv is not None else differ,
            "after": (_join(av) or tr(NONE)) if av is not None else differ}


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
    """The file entries a selection stands for."""
    files = (album or {}).get("files") or []
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
    __slots__ = ("kind", "parent", "row", "kids", "album", "data", "__weakref__")

    def __init__(self, kind, parent, row, album=None, data=None):
        self.kind, self.parent, self.row, self.album, self.data = kind, parent, row, album, data or {}
        self.kids = None


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
        return self.createIndex(row, col, kids[row])

    def index_of(self, node, col=0):
        return QModelIndex() if node is None or node is self.top else self.createIndex(node.row, col, node)

    def parent(self, idx=QModelIndex()):
        if not idx.isValid():
            return QModelIndex()
        p = idx.internalPointer().parent
        return QModelIndex() if p is None or p is self.top else self.createIndex(p.row, 0, p)

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


def _tone(word_kind):
    return {"ok": style.color("identified"), "hold": style.color("attention"), "extra": style.color("attention"),
            "own": style.color("muted"), "missing": style.color("disabled")}.get(word_kind)


class FoldersModel(LazyTree):
    """Your folders as they are on disk: each album's folder (folders that hold several albums
    once), its sub-folders and files, each marked with where it goes."""
    COLS = (N_("Folder or file"), N_("Goes to"))
    OPENS = ("root", "folder")

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
            return tr("not looked up"), "own"
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
    srcs = [fe.get("src") or "" for fe in a.get("files") or []]
    if all(s.startswith(base) for s in srcs):
        return root
    try:
        return os.path.commonpath([root] + [str(Path(s).parent) for s in srcs])
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
            elif held or a.get("status") in ("listed", "excluded", "skipped"):
                out.append({"fe": fe, "goes": "", "tone": "own", "disc": True})
            else:
                no = t["number"]
                where = tr("{disc} #{track}").format(disc=d["label"], track=no) if multi and no else \
                    d["label"] if multi else tr("track {0}").format(no) if no else ""
                out.append({"fe": fe, "goes": "→ " + where if where else "", "tone": "ok", "disc": True})
    _FILES[id(a)] = (a, stamp, out)
    return out


class AlbumsModel(LazyTree):
    """The albums as they will be: release → discs → tracks, each track with its file."""
    COLS = (N_("Album, disc or track"), N_("From"), N_("Note"))
    OPENS = ("album", "disc")
    join_asked = Signal(object, list)               # (album, folders dropped on it)

    def set_albums(self, albums):
        self.beginResetModel()
        self.top.kids = [Node("album", self.top, i, album=a) for i, a in enumerate(albums)]
        self.endResetModel()

    def make_kids(self, node):
        if node.kind == "album":
            return [Node("disc", node, 0, album=node.album, data=d) for d in album_discs(node.album)]
        if node.kind == "disc":
            return [Node("track", node, 0, album=node.album, data=t) for t in node.data["tracks"]]
        return []

    def data(self, idx, role=R_DISPLAY):
        if not idx.isValid():
            return None
        n, c = idx.internalPointer(), idx.column()
        a = n.album
        if role in (R_DISPLAY, R_ACCESSIBLETEXT):
            if n.kind == "album":
                return (album_title(a), album_facts(a), label_for(a)[1])[c]
            if n.kind == "disc":
                d = n.data
                folders = d["folders"]
                src = ("“" + Path(folders[0]).name + "”" + (tr(" and {0} more").format(len(folders) - 1)
                                                            if len(folders) > 1 else "")) if folders else ""
                have = sum(1 for t in d["tracks"] if t["fe"] is not None)
                return (d["label"], src, (f"{have}/{len(d['tracks'])}" if not d.get("extra") else
                                          plural(have, tr("file"), tr("files"))))[c]
            t = n.data
            num = f"{t['number']:02d}  " if t["number"] else ""
            return (num + t["title"], Path(t["fe"]["src"]).name if t["fe"] else "", t["note"])[c]
        if role == R_TOOLTIP:
            if n.kind == "disc" and c == 1:
                return "\n".join(n.data["folders"])
            if n.kind == "track" and n.data["fe"]:
                return n.data["fe"]["src"]
            if n.kind == "album":
                return a.get("source_dir")
            return None
        if role == R_FOREGROUND:
            if n.kind == "track" and n.data["missing"]:
                return QColor(style.color("disabled"))
            if c == 2 and n.kind == "track" and n.data["note"]:
                return QColor(style.color("attention"))
            if c == 2 and n.kind == "album":
                from .style import status_color
                return status_color(a.get("status"))
            if c == 1:
                return QColor(style.color("muted"))
        if role == R_FONT and n.kind in ("album", "disc") and c == 0:
            f = style.font("body")
            f.setWeight(QFont.Weight(600))
            return f
        if role == R_DECORATION and c == 0:
            return icons.icon({"album": "library", "disc": "disc-3"}.get(n.kind, "tag"),
                              "disabled" if n.kind == "track" and n.data["missing"] else "muted", 14)
        return None

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.internalPointer().kind == "album":
            f |= Qt.ItemIsDropEnabled
        return f

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


# --------------------------------------------------------------------------
# tag changes
# --------------------------------------------------------------------------
class TagModel(QAbstractTableModel):
    COLS = ("", N_("Tag"), N_("Original"), N_("New"))
    SEGMENTS = R_USER + 3                         # [(text, changed)] for the delegate
    EDIT_ROLE = R_USER + 1

    def __init__(self):
        super().__init__()
        self.rows, self.editable, self.typed = [], False, None

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
        return self.editable and not r.get("path")

    def flags(self, idx):
        f = super().flags(idx)
        if idx.isValid() and idx.column() == 3 and self.can_edit(self.rows[idx.row()]):
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
            return QColor(style.color({"+": "accent", "→": "attention", "−": "error"}.get(r["mark"], "muted")))
        if role == R_FOREGROUND and c == 1 and r["mark"] == "=":
            return QColor(style.color("muted"))
        if role == R_TEXTALIGNMENT and c == 0:
            return int(Qt.AlignCenter)
        if role == R_FONT and r["mine"] and c in (1, 3):
            f = style.font("body")
            f.setItalic(True)
            return f
        if role == self.EDIT_ROLE:
            return _join(r["after_vals"] or []) if r["after_vals"] is not None else ""
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
class VerticalLabel(QWidget):
    """A folded pane's title, read from the bottom up."""

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.setAccessibleName(text)
        f = style.font("caption")
        f.setWeight(QFont.Weight(600))
        self.setFont(f)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.height() + 8, fm.horizontalAdvance(self.text) + 16)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(QColor(style.color("muted")))
        p.translate(0, self.height())
        p.rotate(-90)
        p.drawText(8, 0, self.height() - 8, self.width(), int(Qt.AlignLeft | Qt.AlignVCenter), self.text)
        p.end()


class Pane(QFrame):
    """A titled pane with a fold button; folded, a side pane is a strip with its title read
    upwards, the bottom pane its title row alone."""
    folded = Signal(str, bool)
    folding = Signal(str)                            # (about to fold: its width as it is now kept)

    def __init__(self, key, title, subtitle="", bottom=False):
        super().__init__()
        self.key, self.title, self.bottom = key, title, bottom
        self.setObjectName("previewPane")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAccessibleName(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(12, 8, 6, 6)
        hl.setSpacing(8)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "caption")
        f = style.font("caption")
        f.setWeight(QFont.Weight(600))
        f.setCapitalization(QFont.AllUppercase)
        self.title_label.setFont(f)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # (a narrow pane cuts it)
        self.title_label.setToolTip(title)
        words.addWidget(self.title_label)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setProperty("role", "hint")
        self.subtitle.setWordWrap(False)
        self.subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.subtitle.setVisible(bool(subtitle))
        words.addWidget(self.subtitle)
        hl.addLayout(words, 1)
        self.extras = QHBoxLayout()
        self.extras.setSpacing(4)
        hl.addLayout(self.extras)
        self.button = self._fold_button()
        hl.addWidget(self.button, 0, Qt.AlignTop)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)
        lay.addWidget(self.body, 1)
        self.strip = QWidget()                              # (folded side pane: button, then the title)
        sl = QVBoxLayout(self.strip)
        sl.setContentsMargins(2, 6, 2, 6)
        sl.setSpacing(8)
        self.strip_button = self._fold_button()
        sl.addWidget(self.strip_button, 0, Qt.AlignHCenter)
        sl.addWidget(VerticalLabel(title), 0, Qt.AlignHCenter)
        sl.addStretch(1)
        self.strip.hide()
        lay.addWidget(self.strip, 1)
        self.collapsed = False
        self._extras = []                                    # (control, words only, its width with words)
        self._words()

    def _fold_button(self):
        b = QToolButton()
        b.setProperty("role", "tool")
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.StrongFocus)
        b.setFixedSize(28, 28)
        b.clicked.connect(lambda _=False: self.set_collapsed(not self.collapsed, by_you=True))
        return b

    def _words(self):
        words = (tr("Expand {0}") if self.collapsed else tr("Collapse {0}")).format(self.title)
        glyph = ("⌃" if self.collapsed else "⌄") if self.bottom else ("›" if self.collapsed else "‹")
        for b in (self.button, self.strip_button):
            b.setText(glyph)
            b.setToolTip(words)
            b.setAccessibleName(words)

    def add(self, w, stretch=0):
        self.body_lay.addWidget(w, stretch)

    def add_extra(self, w, words=False):
        """A control in the title row. Where the pane is narrow a button shows its icon alone (its
        words in its tooltip and accessible name) and WORDS (a label beside a control) give way."""
        self.extras.addWidget(w)
        self._extras.append((w, words, w.sizeHint().width()))
        self._fit_extras()

    def _fit_extras(self):
        if not self._extras:
            return
        need = self.title_label.sizeHint().width() + sum(full for _w, _x, full in self._extras) + 60
        narrow = self.width() < need
        for w, words, _full in self._extras:
            if words:
                w.setVisible(not narrow)
            elif isinstance(w, QToolButton):
                w.setToolButtonStyle(Qt.ToolButtonIconOnly if narrow else Qt.ToolButtonTextBesideIcon)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_extras()

    def set_collapsed(self, on, by_you=False):
        if by_you and on and not self.collapsed:
            self.folding.emit(self.key)
        self.collapsed = bool(on)
        self.body.setVisible(not on)
        if self.bottom:
            self.setMaximumHeight(self.head.sizeHint().height() + 4 if on else 16777215)
        else:
            self.head.setVisible(not on)
            self.strip.setVisible(on)
            self.setMinimumWidth(STRIP if on else SIDE_MIN)
            self.setMaximumWidth(STRIP if on else 16777215)
        self._words()
        if by_you:
            (self.strip_button if on and not self.bottom else self.button).setFocus()
            self.folded.emit(self.key, self.collapsed)


# --------------------------------------------------------------------------
# cover art
# --------------------------------------------------------------------------
class CoverStrip(QListWidget):
    """The covers there are for the album, side by side."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setIconSize(QSize(64, 64))
        self.setGridSize(QSize(168, COVER_ROW - 8))
        self.setWordWrap(True)
        self.setSpacing(6)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAccessibleName(tr("Covers for the album"))


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
class PreviewPanes(QWidget):
    """Your folders | Albums as they will be | Tag changes, over Cover art. owner: the Overview
    (its window's inspector edits, Change Release and Match Files Manually)."""
    album_chosen = Signal(object)                    # an album chosen here: the list follows
    loaded = Signal(str)                             # a background read finished ("tags", "covers")

    def __init__(self, owner=None):
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
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.hsplit = QSplitter(Qt.Horizontal)
        self.hsplit.setChildrenCollapsible(False)
        self.panes = {}
        # ---- Your folders
        p = self.panes["folders"] = Pane("folders", tr("Your folders"), tr("As they are on disk, marked with where "
                                                                           "each goes"))
        self.folders = FoldersModel()
        self.folder_view = self._tree(self.folders, tr("Your folders"), (110,))
        self.folder_view.setDragEnabled(True)
        self.folder_view.setDragDropMode(QAbstractItemView.DragOnly)
        p.add(self.folder_view, 1)
        # ---- Albums as they will be
        p = self.panes["albums"] = Pane("albums", tr("Albums as they will be"),
                                        tr("Release, its discs and the folder each came from, its tracks and the "
                                           "file on each"))
        self.release_btn = QToolButton()
        self.release_btn.setProperty("role", "tool")
        self.release_btn.setText(tr("Change Release…"))
        self.release_btn.setIcon(icons.icon("disc-3", "ink"))
        self.release_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.release_btn.setAutoRaise(True)
        self.release_btn.setAccessibleName(tr("Change Release"))
        self.release_btn.setToolTip(tr("Use another release for the selected album (Album › Change Release)"))
        self.release_btn.clicked.connect(self.change_release)
        p.add_extra(self.release_btn)
        self.album_model = AlbumsModel()
        self.album_model.join_asked.connect(self.join)
        self.album_view = self._tree(self.album_model, tr("Albums as they will be"), (150, 90))
        self.album_view.setAcceptDrops(True)
        self.album_view.setDragDropMode(QAbstractItemView.DropOnly)
        self.album_view.setDropIndicatorShown(True)
        p.add(self.album_view, 1)
        self.why = QLabel("")
        self.why.setObjectName("whyBox")
        self.why.setWordWrap(True)
        self.why.setTextFormat(Qt.RichText)
        self.why.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.why.setContentsMargins(12, 8, 12, 8)
        self.why.setAccessibleName(tr("Why this release"))
        self.why_box = QScrollArea()
        self.why_box.setWidgetResizable(True)
        self.why_box.setFrameShape(QFrame.NoFrame)
        self.why_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.why_box.setWidget(self.why)
        self.why_box.setMaximumHeight(WHY_MOST)
        p.add(self.why_box)
        # ---- Tag changes
        p = self.panes["tags"] = Pane("tags", tr("Tag changes"), "")
        from .rows import Switch
        self.changes_only = Switch(tr("Changes only"))
        self.changes_only.setAccessibleName(tr("Show only the tags that change"))
        self.changes_only.setChecked(bool(self._prefs().get("changes_only", True)))
        self.changes_only.toggled.connect(lambda on: (self._save(changes_only=bool(on)), self.fill_tags()))
        only = QLabel(tr("Changes only"))
        only.setProperty("role", "caption")
        only.setBuddy(self.changes_only)
        p.add_extra(only, words=True)
        p.add_extra(self.changes_only)
        self.tag_model = TagModel()
        self.tag_model.typed = self.typed
        self.tag_view = QTableView()
        self.tag_view.setModel(self.tag_model)
        self.tag_view.setAccessibleName(tr("Tag changes: original and new values"))
        self.tag_view.verticalHeader().hide()
        self.tag_view.setShowGrid(False)
        self.tag_view.setAlternatingRowColors(True)
        self.tag_view.setWordWrap(False)
        self.tag_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tag_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tag_view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tag_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.diff = DiffDelegate(self.tag_view)
        for c in (2, 3):
            self.tag_view.setItemDelegateForColumn(c, self.diff)
        left_header(self.tag_view)
        h = self.tag_view.horizontalHeader()
        h.setMinimumSectionSize(24)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 28)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.resizeSection(1, 130)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        p.add(self.tag_view, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 4, 8, 6)
        self.tag_note = QLabel("")
        self.tag_note.setProperty("role", "caption")
        self.tag_note.setWordWrap(True)
        foot.addWidget(self.tag_note, 1)
        self.revert_btn = QToolButton()
        self.revert_btn.setProperty("role", "tool")
        self.revert_btn.setIcon(icons.icon("history", "ink"))
        self.revert_btn.setText(tr("Revert to Original"))
        self.revert_btn.setToolTip(tr("Revert to Original: the files' own value for the selected tag"))
        self.revert_btn.setAccessibleName(tr("Revert to Original"))
        self.revert_btn.setAutoRaise(True)
        self.revert_btn.clicked.connect(self.revert_selected)
        foot.addWidget(self.revert_btn)
        holder = QWidget()
        holder.setLayout(foot)
        p.add(holder)
        self.tag_view.selectionModel().selectionChanged.connect(lambda *_: self._tag_buttons())
        for k in ("folders", "albums", "tags"):
            self.hsplit.addWidget(self.panes[k])
        self.hsplit.setStretchFactor(0, 1)
        self.hsplit.setStretchFactor(1, 2)
        self.hsplit.setStretchFactor(2, 2)
        # ---- Cover art
        p = self.panes["covers"] = Pane("covers", tr("Cover art"), tr("The front cover is written into the files; "
                                                                      "your scans move with the album"), bottom=True)
        self.cover_buttons = {}
        for key, words, icon, tip in (
                ("use", tr("Use as Front Cover"), "check", tr("Use the selected picture as the album's front cover")),
                ("find", tr("Find Online…"), "search", tr("Every cover the cover sources find (Discogs, Cover Art "
                                                          "Archive …), to pick one by eye")),
                ("file", tr("Choose a File…"), "image", tr("Any image you have")),
                ("make", tr("Generate…"), "sparkles", tr("Clean up your scan or generate a cover with your image "
                                                          "service (Make Covers)"))):
            b = QToolButton()
            b.setProperty("role", "tool")
            b.setText(words)
            b.setIcon(icons.icon(icon, "ink"))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setAccessibleName(words.rstrip("…"))
            b.clicked.connect(lambda _=False, k=key: self.cover_action(k))
            p.add_extra(b)
            self.cover_buttons[key] = b
        self.cover_strip = CoverStrip()
        self.cover_strip.setMinimumHeight(56)                # (a short window: the strip gives way first)
        self.cover_strip.setMaximumHeight(COVER_ROW)
        self.cover_strip.itemDoubleClicked.connect(lambda _it: self.cover_action("use"))
        self.cover_strip.currentItemChanged.connect(lambda *_: self._cover_buttons())
        p.add(self.cover_strip)
        self.cover_note = QLabel("")
        self.cover_note.setProperty("role", "caption")
        self.cover_note.setWordWrap(True)
        self.cover_note.setContentsMargins(12, 0, 12, 6)
        p.add(self.cover_note)
        self.vsplit.addWidget(self.hsplit)
        self.vsplit.addWidget(p)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setStretchFactor(1, 0)
        lay.addWidget(self.vsplit)
        # ---- remembered per user
        prefs = self._prefs()
        self.open_widths = {}
        sizes = prefs.get("sizes")
        if isinstance(sizes, list) and len(sizes) == 3 and all(isinstance(x, int) and x > 0 for x in sizes):
            self.open_widths = dict(zip(self.SIDES, sizes))
        for k, pane in self.panes.items():
            pane.set_collapsed(bool((prefs.get("collapsed") or {}).get(k)))
            pane.folded.connect(self.pane_folded)
            pane.folding.connect(self.keep_open_widths)
        if self.open_widths:
            self.hsplit.setSizes(sizes)
        else:
            self.hsplit.setSizes([260, 420, 460])
        vs = prefs.get("vsizes")
        if isinstance(vs, list) and len(vs) == 2 and all(isinstance(x, int) and x > 0 for x in vs):
            self.vsplit.setSizes(vs)
        else:
            self.vsplit.setSizes([420, 190])
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
        self._changed = []
        self.show_scope()

    # ---- settings ---------------------------------------------------------------------------
    @staticmethod
    def _prefs() -> dict:
        p = _gui_prefs().get(PREF)
        return p if isinstance(p, dict) else {}

    def _save(self, **kw):
        p = self._prefs()
        p.update(kw)
        _set_gui_pref(PREF, p)

    SIDES = ("folders", "albums", "tags")

    def keep_open_widths(self, *_):
        for k, w in zip(self.SIDES, self.hsplit.sizes()):
            if not self.panes[k].collapsed and w > 0:
                self.open_widths[k] = w

    def save_sizes(self):
        """The widths (and the cover row's height) as you left them; a folded pane keeps the
        width it had open."""
        p = self._prefs()
        self.keep_open_widths()
        sizes = [self.open_widths.get(k, s) if self.panes[k].collapsed else s
                 for k, s in zip(self.SIDES, self.hsplit.sizes())]
        if all(s > 0 for s in sizes):
            p["sizes"] = sizes
        if not self.panes["covers"].collapsed and all(s > 0 for s in self.vsplit.sizes()):
            p["vsizes"] = self.vsplit.sizes()
        _set_gui_pref(PREF, p)

    def reopen_width(self, key):
        if self.panes[key].collapsed or key not in self.open_widths:
            return
        sizes = self.hsplit.sizes()
        i = self.SIDES.index(key)
        others = [j for j in range(3) if j != i and not self.panes[self.SIDES[j]].collapsed]
        room = sum(sizes) - sum(sizes[j] for j in range(3) if j != i and j not in others)
        want = max(SIDE_MIN, min(self.open_widths[key], room - SIDE_MIN * len(others)))
        rest = room - want                                   # (the other open panes share the rest as they were)
        had = sum(sizes[j] for j in others) or 1
        for n, j in enumerate(others):
            sizes[j] = rest - sum(sizes[o] for o in others[:n]) if n == len(others) - 1 else \
                max(SIDE_MIN, rest * sizes[j] // had)
        sizes[i] = want
        self.hsplit.setSizes(sizes)
        self.save_sizes()

    def pane_folded(self, key, on):
        if key == "covers" and not on:
            self.load_covers()                               # (read once the strip is open)
        p = self._prefs()
        p.setdefault("collapsed", {})[key] = bool(on)
        _set_gui_pref(PREF, p)
        if key != "covers":
            if not on and key in self.open_widths:          # (opened: the width it had, once it may grow)
                QTimer.singleShot(0, self, lambda k=key: self.reopen_width(k))
            else:
                self.save_sizes()

    # ---- the trees --------------------------------------------------------------------------
    def _tree(self, model, name, widths=(120, 120)):
        v = QTreeView()
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
        """The plan's albums (the list's own entries, in its order)."""
        t0 = time.perf_counter()
        self.albums = albums if albums is not None else []
        forget()
        keep = self.scope
        self._quiet = True
        try:
            self.folders.set_albums(self.albums)
            self.album_model.set_albums(self.albums)
        finally:
            self._quiet = False
        self.opened_at = time.perf_counter() - t0
        if keep and any(a is keep[0] for a in self.albums):
            self.show_scope(*keep)
        else:
            self.show_scope(None)

    def album_changed(self, album):
        """ALBUM's entry changed (your edit, its look-up): its rows made again, when next shown."""
        self._changed.append(album)
        self._refresh_timer.start()

    def _refresh_changed(self):
        changed, self._changed = self._changed, []
        if not changed:
            return
        ids = {id(a) for a in changed}
        self._quiet = True
        try:
            for a in changed:
                forget(a)
            for n in self.album_model.top.kids:
                if id(n.album) in ids:
                    self.album_model.reset_kids(n)
                    self.album_model.dataChanged.emit(self.album_model.index_of(n, 0), self.album_model.index_of(n, 2))
            for n in self.folders.top.kids:
                if any(id(a) in ids for a in n.data["albums"]):
                    self.folders.reset_kids(n)
                    self.folders.dataChanged.emit(self.folders.index_of(n, 0), self.folders.index_of(n, 1))
        finally:
            self._quiet = False
        if self.scope and id(self.scope[0]) in ids:
            self.show_scope(*self.scope)

    # ---- selection, kept in step --------------------------------------------------------------
    def show_album(self, album):
        """The list chose ALBUM (None: none, or several): shown here, a disc, track or file of it
        already chosen kept."""
        if album is None:
            if self.scope is not None:
                self.show_scope(None)
            return
        if self.scope and self.scope[0] is album:
            return
        self.show_scope(album, "album", None)

    def picked(self, view, idx):
        if self._quiet or not idx.isValid():
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
        before = self.scope[0] if self.scope else None
        self.show_scope(*scope, source=view)
        if scope[0] is not before:
            self.album_chosen.emit(scope[0])

    def show_scope(self, album=None, kind="album", key=None, source=None):
        self.scope = (album, kind, key) if album is not None else None
        self._quiet = True
        try:
            if source is not self.folder_view:
                self._highlight(self.folder_view, self._folder_node(album, kind, key))
            if source is not self.album_view:
                self._highlight(self.album_view, self._album_node(album, kind, key))
        finally:
            self._quiet = False
        self.release_btn.setEnabled(album is not None)
        self.show_why(album)
        self.read_tags()
        self.fill_tags()
        if album is not self._cover_album or album is None:
            self.load_covers()

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
        top = next((n for n in self.album_model.top.kids if n.album is album), None)
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
            before = self.scope[0] if self.scope else None
            self.show_scope(album, kind, key)
            if album is not before:
                self.album_chosen.emit(album)

    # ---- why ----------------------------------------------------------------------------------
    def show_why(self, album):
        if album is None:
            self.why.setText("")
            self.why_box.hide()
            return
        title, lines = why_lines(album)
        colour = {"✓": style.color("identified"), "≠": style.color("attention"), "→": style.color("muted")}
        body = "".join(f"<tr><td style='color:{colour[m]};font-weight:600;padding-right:8px'>{m}</td>"
                       f"<td>{html.escape(w)}</td></tr>" for m, w in lines[:8])
        self.why.setText(f"<b>{html.escape(title)}</b><table cellspacing='0'>{body}</table>" if lines else
                         f"<b>{html.escape(title)}</b><br>{html.escape(tr('Nothing recorded for this album.'))}")
        self.why.setAccessibleName(title)
        self.why_box.show()
        self.why_box.setMaximumHeight(min(WHY_MOST, self.why.heightForWidth(max(200, self.why_box.width())) + 4))

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
        self.current = {}
        files = [fe["src"] for fe in self.scope_files()][:READ_MOST]
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
        w_ref[0] = self.tag_worker = start(self, work, done, failed=lambda _m: None)

    def editable(self) -> bool:
        if not self.scope or self.owner is None:
            return False
        insp = getattr(getattr(self.owner, "win", None), "inspector", None)
        return bool(insp is not None and self.scope_files() and insp.can_edit(self.scope[0]))

    def fill_tags(self):
        title, sub = self.scope_words()
        p = self.panes["tags"]
        say(p.subtitle, tr("Selection"), f"{title} · {sub}" if sub else title)
        p.subtitle.setVisible(True)
        files = self.scope_files()
        rows = tag_rows(files, self.current) if files else []
        if self.changes_only.isChecked():
            rows = [r for r in rows if r["mark"] != "="]
        self.tag_model.set_rows(rows, self.editable())
        n = sum(1 for r in rows if r["mark"] != "=")
        if not self.scope:
            note = tr("Select a folder, album, disc or track to see its tag changes.")
        elif not files:
            note = sub
        elif not rows:
            note = tr("No tag changes for this selection.")
        else:
            note = tr("{0} for {1}.").format(plural(n, tr("change"), tr("changes")),
                                             plural(len(files), tr("file"), tr("files")))
            if self.editable():
                note += " " + tr("Double-click a new value to edit it; nothing is written until you apply.")
        self.tag_note.setText(note)
        self._tag_buttons()

    def selected_tag(self):
        idx = self.tag_view.currentIndex()
        if not idx.isValid() or not self.tag_view.selectionModel().hasSelection():
            return None
        return self.tag_model.rows[idx.row()] if 0 <= idx.row() < len(self.tag_model.rows) else None

    def _tag_buttons(self):
        r = self.selected_tag()
        self.revert_btn.setEnabled(bool(r) and self.tag_model.can_edit(r) and (r["mark"] != "=" or r["mine"]))

    def _edit(self, field_, value):
        """Your value for FIELD on the selection, through the inspector's edits."""
        insp = getattr(getattr(self.owner, "win", None), "inspector", None)
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
        text = (text or "").strip()
        if row.get("path"):
            return
        if row["after_vals"] is not None and text == _join(row["after_vals"]):
            return
        self._edit(row["field"], [text] if text and text != tr(NONE) else None)

    def revert_selected(self):
        import plan as planmod
        r = self.selected_tag()
        if r and self.tag_model.can_edit(r):
            self._edit(r["field"], dict(planmod.KEEP))

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
        self.panes["covers"].subtitle.setText(album_title(album) if album else tr("Select an album"))
        self._cover_buttons()
        if album is None or self.panes["covers"].collapsed or self.owner is None:
            self.cover_note.setText("")
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
        ref[0] = self.cover_worker = start(self, work, done, failed=failed)

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
            it.setToolTip(c.extra.get("path") or c.extra.get("file") or c.source)
            it.setData(R_ACCESSIBLETEXT, words.replace("\n", ", ") + (f", {size}" if size else ""))
            self.cover_strip.addItem(it)
        n_own = sum(1 for c in self.covers if c.kind == "own")
        has_new = any(c.kind == "new" for c in self.covers)
        self.cover_note.setText(
            (tr("The new front cover first, then your own pictures.") if has_new else
             tr("No new cover: the album keeps its own.")) + " " +
            (tr("{0} of your own.").format(n_own) if n_own else tr("No pictures of your own.")) + " " +
            tr("Find Online looks for more."))
        self._cover_buttons()

    def chosen_cover(self):
        it = self.cover_strip.currentItem()
        return self.covers[it.data(R_USER)] if it is not None and 0 <= it.data(R_USER) < len(self.covers) \
            else None

    def _insp(self):
        return getattr(getattr(self.owner, "win", None), "inspector", None)

    def _cover_buttons(self):
        insp = self._insp()
        album = self.scope[0] if self.scope else None
        on = bool(insp is not None and album is not None and insp.can_edit(album))
        c = self.chosen_cover()
        self.cover_buttons["use"].setEnabled(on and c is not None and c.kind != "new")
        for k in ("find", "file", "make"):
            self.cover_buttons[k].setEnabled(on)

    def cover_action(self, key):
        """The cover strip's commands: each the inspector's own (its Artwork's Find Covers…,
        Choose Image…, the Find Covers sheet's Make Covers…, and a picture used as Choose Image
        uses one)."""
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
        elif key == "make":
            from . import sheets
            from .cover_chooser import CoverChooser
            dlg = CoverChooser(insp, album)                # (the Find Covers sheet, its Make Covers… asked)
            self._chooser = dlg
            QTimer.singleShot(0, dlg, dlg.make_ai)
            sheets.run(dlg, "covers")

    # ---- the list's commands ----------------------------------------------------------------
    def change_release(self):
        album = self.scope[0] if self.scope else None
        if album is None or self.owner is None:
            return
        self.album_chosen.emit(album)                    # (the list's selection first: its command)
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
    def stop(self):
        for w in (self.tag_worker, self.cover_worker):
            if w is not None:
                w.cancelled = True
        self.tag_worker = self.cover_worker = None
