"""gui/manual_match.py - Match Files Manually: Picard's two-pane album view, as a sheet.

The album's files on the left (an unresolved album's files are loose files: no release yet),
the release's tracks on the right: the release the plan chose, or one picked with Choose
Release… (the search sheet of gui.search, its choice handed back here) or pasted as a link.
The pairs start from an automatic match by disc and track number, title and length (the plan's
own pairing when the release is the plan's); drag files onto a track, or select a file and a
track and press Match (Alt+M); Unmatch (Delete) takes a pair apart. Each pair shows how much
longer or shorter the file is than the track, unmatched files and tracks are said so.

Nothing is written here. Use These Matches turns the pairs into your edits of the album
(plan.apply_edits' shape, kept in the inspector's edits): each file matched to another track
than the plan gave it takes that track's own fields (track_keys: TRACK_FIELDS and whatever differs
between the release's tracks; the planned track's own fields the new one lacks are removed), a
file left unmatched keeps its own tags (plan.KEEP on what the plan would have changed), and the
pairs themselves are kept in the edit as edit["match"] (MATCH_KEY: the pairs, the files left
unmatched, what the match set, the release chosen before; for this sheet and a planner that takes
them). Then the album is previewed again - with the release chosen (gui.search.force_release)
when it is not the plan's - and Apply writes it as any plan: staged, verified, journaled and
undoable. Until the planner lines files up by edit["match"] (ENGINE_TAKES_MATCHES), another
release than the plan's can be used only with every file matched and as many tracks as files.
plan.apply_edits names and places a file after the track it is matched to (ENGINE_RENAMES_BY_MATCH:
a swapped file takes the name the plan gave the file whose track it now has, with its own
extension; a file left unmatched stays where it is), so an album the plan renames or moves can be
matched freely.
Lookups and reading the files run on workers (gui.work.start).

Entry points:
- open_manual_match(page, album=None, ref=None) -> the sheet (page: the Overview: selected(),
  preview(), forced, running, profile, win.inspector)
- can_match(album) -> bool, for enabling the command
- COMMANDS / MENU / handlers(ov) / update_commands(commands, page, ready): the Album menu's
  "Match Files Manually…" (Ctrl+Shift+M), in gui.actions.COMMANDS' form
- add_dropped(win, paths) -> list: folders and audio files dropped on the main window, the ones
  the preview already holds left out (said in the status bar); the first new one is previewed
- auto_match, score, release_tracks, file_rows, as_edit: the matching itself (no Qt)."""
from __future__ import annotations

import copy
import difflib
import os
import re
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import icons, sheets, style
from .common import heading, left_header, plural, say
from .i18n import N_, tr
from .work import start

MIME = "application/x-tagmender-files"           # files dragged from the left pane (one path a line)
MATCH_KEY = "match"                              # an edit's pairs: {"ref", "pairs": {file: index}, "unmatched",
#                                                  "set": {file: {field: value the match wrote}}, "was_forced"}
# a track's own fields: what a file matched to another track takes from it (the album's fields
# are the release's for every file alike); with these, any field whose values differ between the
# release's tracks (track_keys: per-recording genre, credits ...)
TRACK_FIELDS = ("title", "artist", "artists", "artistsort", "musicbrainz_artistid", "tracknumber", "totaltracks",
                "discnumber", "totaldiscs", "discsubtitle", "media", "musicbrainz_recordingid",
                "musicbrainz_trackid", "isrc", "composer", "composersort", "musicbrainz_composerid", "lyricist",
                "writer", "arranger", "conductor", "producer", "engineer", "mixer", "remixer", "musicbrainz_workid",
                "work", "license")
TRACK_PREFIXES = ("performer:",)
MOVE_MIME = "application/x-tagmender-matched"    # a track's file dragged off it (back to the files, or another track)
# a pair's score (0..1): the title counts most, then the track number, then the length
W_TITLE, W_NUMBER, W_LENGTH = 0.5, 0.3, 0.2
LENGTH_SPAN = 30.0                               # seconds apart at which the length no longer counts
THRESHOLD = 0.35                                 # the least score the automatic match pairs on
# True once the planner lines files up with the tracks by edit["match"] and keeps the files in
# edit["match"]["unmatched"] as they are (plan.KEEP on every field it plans for them): until then
# another release than the plan's can be matched only with as many tracks as files, each matched
# (the planner pairs files and tracks by position, and an unmatched file would take its track)
ENGINE_TAKES_MATCHES = False
# True: plan.apply_edits names and places a file after the track it is matched to (plan._place_by_match:
# the planned name of the file that had that track, a file left unmatched kept where it is). When
# False, an album whose files the plan renames or moves could be matched only as the plan paired it
ENGINE_RENAMES_BY_MATCH = True

BUSY = N_("Finish or cancel the current work first.")
HINT = (N_("Drag files onto the tracks they are, or select a file and a track and press Match. Nothing is "
           "written until you apply the preview."))


# --------------------------------------------------------------------------
# the matching (no Qt)
# --------------------------------------------------------------------------
def _number(v) -> int:
    """3 from "3", "03", "3/12", ["3"]; 0 when none."""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def _first(v) -> str:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v or "")


def _vals(v) -> list:
    """A tag's values as a list of strings ([] when none)."""
    if v is None or v == "":
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def _words(s) -> str:
    return " ".join(re.findall(r"\w+", str(s or "").casefold()))


def title_similarity(a, b) -> float:
    a, b = _words(a), _words(b)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def _secs(length) -> float:
    """Seconds from "3:25" or "1:02:03" (mb_tags.format_time); 0 when none."""
    parts = str(_first(length)).split(":")
    try:
        n = 0.0
        for p in parts:
            n = n * 60 + float(p)
        return n if len(parts) > 1 else 0.0
    except ValueError:
        return 0.0


def clock(secs) -> str:
    """"3:25" (an hour and more: "1:02:03"); "" when unknown."""
    if not secs:
        return ""
    s = round(float(secs))
    if s < 3600:
        return "%d:%02d" % divmod(s, 60)
    h, rest = divmod(s, 3600)
    return "%d:%02d:%02d" % ((h,) + divmod(rest, 60))


def length_diff(f: dict, t: dict):
    """(seconds the file is longer, + or -, within the matcher's tolerance) or (None, True) when
    a length is unknown."""
    import match_core as mc
    if not f.get("secs") or not t.get("secs"):
        return None, True
    return f["secs"] - t["secs"], mc.duration_ok(f["secs"], t["secs"], mc.DEFAULT_CFG)


def diff_words(d) -> str:
    """"+0:03" (the file 3 seconds longer), "−1:10", "same length"."""
    if d is None:
        return ""
    if abs(d) < 0.5:
        return tr("same length")
    return ("+" if d > 0 else "−") + clock(abs(d))


def score(f: dict, t: dict) -> float:
    """How well file f fits track t (0..1): title, disc and track number, length (a length
    nobody knows counts half)."""
    s = W_TITLE * title_similarity(f.get("title"), t.get("title"))
    disc_ok = not f.get("disc") or not t.get("disc") or f["disc"] == t["disc"]
    if f.get("number") and f["number"] == t.get("number") and disc_ok:
        s += W_NUMBER
    if f.get("secs") and t.get("secs"):
        s += W_LENGTH * max(0.0, 1.0 - abs(f["secs"] - t["secs"]) / LENGTH_SPAN)
    else:
        s += W_LENGTH / 2
    return s


def auto_match(files: list, tracks: list, keep: dict | None = None, threshold=THRESHOLD) -> dict:
    """{file path: track index}: the best pairs first, each file and track once, none below
    threshold. keep: pairs already made (yours), left as they are."""
    pairs = dict(keep or {})
    used = set(pairs.values())
    scored = sorted(((score(f, t), i, j) for i, f in enumerate(files) if f["path"] not in pairs
                     for j, t in enumerate(tracks) if j not in used), key=lambda x: (-x[0], x[1], x[2]))
    for s, i, j in scored:
        path = files[i]["path"]
        if s < threshold:
            break
        if path in pairs or j in used:
            continue
        pairs[path] = j
        used.add(j)
    return pairs


def planned_pairs(files: list, tracks: list) -> dict:
    """{file: track index} as the plan has them: each file's planned disc and track number."""
    by = {(t.get("disc") or 1, t.get("number")): j for j, t in enumerate(tracks)}
    out = {}
    for f in files:
        j = by.get((f.get("planned_disc") or 1, f.get("planned_number")))
        if j is not None and f.get("planned_number"):
            out[f["path"]] = j
    return out


def planned_ref(album) -> str:
    """The release the plan tagged the album with ("" when none was proven: a recording found by
    its fingerprint, an album only, a web source that is no MusicBrainz or Discogs release)."""
    import engine
    ident = (album or {}).get("identification") or {}
    rid = str(ident.get("release_id") or "").strip()
    if not rid or ident.get("status") != "identified" or ident.get("tier") == "recording":
        return ""
    prov = str(ident.get("provider") or "").casefold()
    if prov.startswith("web:"):
        prov = prov[4:].strip()
    if prov.startswith("musicbrainz"):
        return rid if engine.parse_release_ref(rid)[0] == "mb" else ""
    if prov.startswith("discogs"):
        ref = rid if rid.casefold().startswith("discogs:") else f"discogs:{rid}"
        return ref if engine.parse_release_ref(ref)[0] == "discogs" else ""
    return ""


def same_ref(a, b) -> bool:
    import engine
    ka, kb = engine.parse_release_ref(a), engine.parse_release_ref(b)
    return ka[0] is not None and ka == kb


def renames(album) -> bool:
    """The plan renames or moves at least one of the album's files (its dst is not its src)."""
    return any(fe.get("dst") and os.path.realpath(fe["dst"]) != os.path.realpath(fe["src"])
               for fe in (album or {}).get("files") or [])


def album_file_count(album) -> int:
    """The audio files the engine lines up with a release's tracks (written or skipped)."""
    return len({fe["src"] for fe in album.get("files") or []} |
               {s.get("file") for s in album.get("skipped") or [] if s.get("file")})


def file_rows(album) -> list:
    """The album's files as the left pane lists them (reads their tags: on a worker). Each:
    path, name, title, disc, number, secs - the plan's values where it changes them - and the
    planned disc and number (the plan's own pairing)."""
    import match_albums as ma
    out = []
    for fe in album.get("files") or []:
        src = fe["src"]
        info = ma.read_file(Path(src))
        ch = fe.get("changes") or {}
        # (the planner's own values: before your edits, an earlier match among them)
        auto = ((fe.get("auto") or {}).get("changes") if "auto" in fe else ch) or {}

        def val(changes, key, own):
            return _number(changes[key]) if changes.get(key) else int(info.get(own) or 0)
        title = _first(ch["title"]) if ch.get("title") else info.get("title") or ""
        out.append({"path": src, "name": Path(src).name, "title": title or Path(src).stem,
                    "number": val(ch, "tracknumber", "num"), "disc": val(ch, "discnumber", "disc"),
                    "secs": float(info.get("secs") or 0.0),
                    "planned_number": val(auto, "tracknumber", "num"), "planned_disc": val(auto, "discnumber", "disc")})
    return out


def track_keys(all_tags: list) -> set:
    """A release's per-track fields, from every track's tags as the engine writes them: the
    TRACK_FIELDS and performer:* any track has, and any other field whose values are not the same
    on every track ("~" fields are never written)."""
    keys = set().union(*all_tags) if all_tags else set()
    out = set()
    for k in keys:
        if str(k).startswith("~"):
            continue
        vals = [_vals(t.get(k)) for t in all_tags]
        if k in TRACK_FIELDS or str(k).startswith(TRACK_PREFIXES) or any(v != vals[0] for v in vals):
            out.add(k)
    return out


def _own(d: dict, keys) -> dict:
    return {k: _vals(d.get(k)) for k in keys if _vals(d.get(k))}


def release_tracks(lookup, ref, profile=None) -> dict:
    """The release's tracks, in the order of its audio files: {"ref", "title", "artist", "url",
    "tracks": [{"disc", "number", "title", "artist", "secs", "tags"}]} (tags: the track's own
    fields as the engine writes them; MusicBrainz through mb_tags, Discogs through the matcher).
    Raises LookupError in words when the release cannot be had."""
    import engine
    import match_albums as ma
    kind, rid = engine.parse_release_ref(ref)
    if kind is None:
        raise LookupError(tr("Not a MusicBrainz or Discogs release: {ref}").format(ref=ref))
    if kind == "mb":
        if not lookup.use_mb:
            raise LookupError(tr("MusicBrainz is turned off in this profile."))
        import mb_tags
        ps = profile.picard_tags() if profile is not None else None
        rel = lookup.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}?inc={mb_tags.inc_for(ps)}&fmt=json",
                                   "mb")
        if not rel:
            raise LookupError(tr("MusicBrainz has no release {rid}.").format(rid=rid))
        tracks = []
        all_tags = list(mb_tags.release_tags(rel, ps))
        keys = track_keys(all_tags)
        for d in all_tags:
            tracks.append({"disc": _number(d.get("discnumber")) or 1, "number": _number(d.get("tracknumber")),
                           "title": _first(d.get("title")), "artist": _first(d.get("artist")),
                           "secs": _secs(d.get("~length")), "tags": _own(d, keys)})
        who = "".join(c.get("name", "") + c.get("joinphrase", "") for c in rel.get("artist-credit") or []
                      if isinstance(c, dict))
        return {"ref": rid, "title": rel.get("title") or "", "artist": who,
                "url": f"https://musicbrainz.org/release/{rid}", "tracks": tracks}
    if not lookup.use_discogs or lookup.dm is None:
        raise LookupError((lookup.notes or {}).get("discogs") or tr("Discogs is turned off in this profile."))
    rel = lookup.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
    if not rel:
        raise LookupError(tr("Discogs has no release {rid}.").format(rid=rid))
    cand = ma.discogs_candidate(rel, lookup.dm)
    flat = [t for t in cand.get("tracks") or [] if not t.get("video")]
    per_disc, tracks = {}, []
    for t in flat:
        per_disc[t.get("disc") or 1] = per_disc.get(t.get("disc") or 1, 0) + 1
    # each track's own fields as the engine writes a Discogs release (its artists and credits);
    # the master's original year is the album's, so it is not asked for here
    try:
        album_f, per_track, _n = engine.discogs_parts(rel, lookup.dm, None)
        full = engine.discogs_only_tags(album_f, per_track) if per_track is not None else []
    except Exception:
        full = []
    if len(full) != len(flat):
        full = []
    keys = track_keys(full) if full else set()
    seen = {}
    for i, t in enumerate(flat):
        d = t.get("disc") or 1
        seen[d] = seen.get(d, 0) + 1
        if full:
            tags = _own(full[i], keys)
        else:                                          # (the tracklist does not line up: the numbers)
            tags = {k: v for k, v in {"title": [t.get("title") or ""], "tracknumber": [str(seen[d])],
                                      "discnumber": [str(d)], "totaltracks": [str(per_disc[d])],
                                      "totaldiscs": [str(len(per_disc))]}.items() if v[0]}
        tracks.append({"disc": d, "number": seen[d], "title": t.get("title") or "",
                       "artist": _first(tags.get("artist")), "secs": float(t.get("secs") or 0.0), "tags": tags})
    return {"ref": f"discogs:{rid}", "title": cand.get("title") or "", "artist": cand.get("artist") or "",
            "url": cand.get("url") or f"https://www.discogs.com/release/{rid}", "tracks": tracks}


def as_edit(album, files: list, tracks: list, pairs: dict, ref: str, old: dict | None = None) -> dict:
    """The pairs as your edit's file_tags and match, from the album's edit `old` (a copy is
    returned; old is never changed): a file matched to another track than the plan gave it
    takes the track's own fields (the release's per-track fields that track lacks: removed);
    one left unmatched keeps its own values (plan.KEEP) for what the plan would change; a file
    on the plan's own track needs nothing. What an earlier match set is taken back first (a
    field you edited since stays yours); your other edits of a file's tags stay (they win)."""
    import plan as planmod
    e = copy.deepcopy(old) if old else {"files": [fe["src"] for fe in album.get("files") or []], "tags": {},
                                        "file_tags": {}, "cover": None}
    e.setdefault("tags", {})
    e.setdefault("cover", None)
    ft = e.setdefault("file_tags", {})
    for src, wrote in ((e.get(MATCH_KEY) or {}).get("set") or {}).items():
        mine = ft.get(src) or {}
        if not isinstance(wrote, dict):                # (an older edit: the field names only)
            wrote = {k: mine.get(k) for k in wrote}
        for k, v in wrote.items():
            if k in mine and mine[k] == v:             # (changed since: your edit, kept)
                del mine[k]
    same = same_ref(ref, planned_ref(album))
    planned = planned_pairs(files, tracks) if same else {}
    per_track = set().union(*(t.get("tags") or {} for t in tracks)) if tracks else set()
    set_ = {}
    by_src = {fe["src"]: fe for fe in album.get("files") or []}
    for f in files:
        src = f["path"]
        mine = ft.get(src) or {}
        j = pairs.get(src)
        if j is None:
            fe = by_src.get(src) or {}
            planned_ch = (fe.get("auto") or {}).get("changes") if "auto" in fe else fe.get("changes")
            want = {k: planmod.KEEP for k in (planned_ch or {}) if not k.startswith("~") and k not in mine}
        elif same and planned.get(src) == j:
            continue
        else:
            new = {k: None for k in per_track if k not in tracks[j]["tags"]}      # (the planned track's own)
            new.update({k: list(v) for k, v in tracks[j]["tags"].items()})
            want = {k: v for k, v in new.items() if k not in mine}
        if want:
            ft[src] = dict(mine, **want)
            set_[src] = copy.deepcopy(want)
    e["file_tags"] = {k: v for k, v in ft.items() if v}
    e[MATCH_KEY] = {"ref": ref, "pairs": {k: int(v) for k, v in pairs.items()},
                    "unmatched": [f["path"] for f in files if f["path"] not in pairs], "set": set_}
    return e


def can_match(album) -> bool:
    """The album can be matched: it is one the plan may write, and it has files."""
    import plan as planmod
    return bool(album) and album.get("status") in planmod.EDITABLE and bool(album.get("files"))


# --------------------------------------------------------------------------
# the main window's drop: add folders and files not in the preview yet
# --------------------------------------------------------------------------
def _inside(path, root) -> bool:
    p, r = os.path.realpath(str(path)), os.path.realpath(str(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def new_paths(page, paths) -> tuple[list, list]:
    """(the dropped paths the preview does not hold yet, the ones it does). A path is held when
    it is a file of an album listed, a folder holding one, or inside the folder previewed;
    but not the folder of an album taken out of the list (Remove from List): dropped again, it is
    listed again."""
    root = getattr(page, "path", None)
    removed = set(getattr(page, "removed", None) or ())
    plan = getattr(page, "plan", None) or {}
    held = {os.path.realpath(f) for a in plan.get("albums") or []
            for f in [fe.get("src") for fe in a.get("files") or []] + list(a.get("album_files") or []) if f}
    folders = {os.path.dirname(f) for f in held}
    new, present, seen = [], [], set()
    for p in paths or []:
        rp = os.path.realpath(str(p))
        if rp in seen:
            continue
        seen.add(rp)
        # (inside the folder previewed: held, unless an album of it was taken out of the list)
        inside = rp in held or any(_inside(d, rp) for d in folders) or (
            bool(root) and _inside(rp, root) and not any(_inside(d, rp) or _inside(rp, d) for d in removed))
        (present if inside else new).append(str(p))
    return new, present


def add_dropped(win, paths) -> list:
    """Folders and audio files dropped on the window (gui.common.music_paths): the ones the
    preview already holds are left out and said so; the new ones join the list (Overview.add_to_list,
    or add_later during a run). With no list yet the first new one is previewed, the others named
    in the status bar. Returns the new paths. Connect in place of MainWindow.dropped."""
    page = (getattr(win, "pages", None) or {}).get("Overview")
    bar = win.statusBar() if hasattr(win, "statusBar") else None

    def tell(text):
        if bar is not None:
            bar.showMessage(text, 8000)
    if page is None:
        return []
    if getattr(page, "running", False):
        if hasattr(page, "add_later"):                  # (joins the list when the work ends)
            return page.add_later(paths)
        tell(tr(BUSY))
        return []
    new, present = new_paths(page, paths)
    if not new:
        if present:
            tell(tr("Already in the preview: {0}.").format(_names(present)))
        return []
    if getattr(page, "plan", None) and getattr(page, "path", None) and hasattr(page, "add_to_list"):
        page.add_to_list(new)                           # (a list showing: joined, never replaced)
        if present:
            tell(tr("Already in the preview: {0}.").format(_names(present)))
        return new
    folders = [p for p in new if os.path.isdir(p)]
    if len(folders) > 1 and hasattr(page, "set_folders"):
        # several folders at once: all listed together (nothing looked up), you choose what to look up
        page.set_folders(folders)
        if present:
            tell(tr("Already in the preview: {0}.").format(_names(present)))
        return new
    page.set_path(new[0])
    rest = [tr("already in the preview: {0}").format(_names(present))] if present else []
    if len(new) > 1:
        rest.insert(0, tr("drop {0} again once this preview is applied (one folder at a time)").format(_names(new[1:])))
    if rest:
        tell(f"Previewing {_names(new[:1])}; " + "; ".join(rest) + ".")
    return new


def _names(paths, most=3) -> str:
    names = [os.path.basename(str(p).rstrip("/")) or str(p) for p in paths]
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "")


# --------------------------------------------------------------------------
# the Album menu's command
# --------------------------------------------------------------------------
COMMANDS = {
    "match_files": (N_("Match Files &Manually…"), "Ctrl+Shift+M", "arrow-right",
                    N_("Pair each file with the track it is, by dragging or from the keyboard")),
}
MENU = tuple(COMMANDS)


def handlers(ov):
    """The command's action; ov() gives the Overview."""
    return {"match_files": lambda: open_manual_match(ov())}


def update_commands(commands, page, ready):
    """Enabled with the Overview shown, an album selected that the plan may write, nothing running."""
    sel = getattr(page, "selected", None) if ready else None
    album = sel() if callable(sel) else None
    commands["match_files"].setEnabled(bool(ready) and can_match(album))


def open_manual_match(page, album=None, ref=None):
    """The sheet for the selected album (or `album`), with the plan's release or `ref`."""
    if album is None:
        sel = getattr(page, "selected", None)
        album = sel() if callable(sel) else None
    if not can_match(album):
        win = getattr(page, "win", None)
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(tr("Select an album with files to match first."), 4000)
        return None
    old = getattr(page, "_match_sheet", None)
    if old is not None:
        try:
            old.stop()
            old.close()
        except RuntimeError:                           # (already deleted with its window)
            pass
    dlg = ManualMatchSheet(page, album, ref)
    page._match_sheet = dlg
    dlg.open()                                         # (window-modal, never blocks the caller)
    return dlg


# --------------------------------------------------------------------------
# the two panes
# --------------------------------------------------------------------------
def _setup(t, cols, name):
    """A pane's table as the app's tables are: rows selected whole, no grid, the second column wide."""
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels([tr(c) for c in cols])
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.ElideMiddle)
    t.setAccessibleName(name)
    left_header(t)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(1, QHeaderView.Stretch)
    return t


def _dragged(md, kind) -> list:
    return [p for p in bytes(md.data(kind)).decode().split("\n") if p] if md.hasFormat(kind) else []


def _drag(widget, kind, paths):
    mime = QMimeData()
    mime.setData(kind, "\n".join(paths).encode())
    drag = QDrag(widget)
    drag.setMimeData(mime)
    drag.setPixmap(icons.pixmap("arrow-right", style.color("accent"), 24))
    drag.exec(Qt.LinkAction)
    return mime


class FileTable(QTableWidget):
    """The album's files: dragged onto tracks (several at once, in order); a track's file dragged
    back here is unmatched; files dropped from a file manager are offered to the sheet
    (sheet.add_files)."""

    def __init__(self, sheet):
        super().__init__()
        self.sheet = sheet
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def paths(self) -> list:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        return [self.item(r, 0).data(Qt.UserRole) for r in rows if self.item(r, 0)]

    def startDrag(self, _actions):
        paths = self.paths()
        if paths:
            _drag(self, MIME, paths)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME) or md.hasFormat(MOVE_MIME) or md.hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MOVE_MIME):                    # (a track's file dragged back: unmatched)
            e.acceptProposedAction()
            self.sheet.unmatch_paths(_dragged(md, MOVE_MIME))
            return
        if md.hasFormat(MIME):                         # (the pane's own files dropped on it: nothing to do)
            e.acceptProposedAction()
            return
        from .common import music_paths
        paths = music_paths(md)
        if paths:
            e.acceptProposedAction()
            self.sheet.add_files(paths)


class TrackTable(QTableWidget):
    """The release's tracks: files dropped on a track are matched to it (and the tracks after it);
    a track's file dragged off it goes back to the files (unmatched) or onto another track."""

    def __init__(self, sheet):
        super().__init__()
        self.sheet = sheet
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    TITLE_MIN = 140                                   # (the titles, what the files are matched against)
    FILE_MAX = 180

    @staticmethod
    def _ours(md) -> bool:
        return md.hasFormat(MIME) or md.hasFormat(MOVE_MIME)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def fit(self):
        """Title keeps at least TITLE_MIN: File (also in the files' Track column) is capped at
        FILE_MAX (shortened in the middle) and left out when even that leaves Title too little."""
        if self.columnCount() < 5:
            return
        h = self.horizontalHeader()
        want = lambda c: max(self.sizeHintForColumn(c), h.sectionSizeHint(c))  # noqa: E731
        fixed = sum(want(c) for c in (0, 2, 4))
        file_w = min(want(3), self.FILE_MAX)
        narrow = self.viewport().width() - fixed - file_w < self.TITLE_MIN
        self.setColumnHidden(3, narrow)
        if not narrow:
            self.setColumnWidth(3, file_w)

    def paths(self) -> list:
        """The selected track's file (its File column), when it has one."""
        rows = sorted({i.row() for i in self.selectedIndexes()})
        it = self.item(rows[0], 3) if rows else None
        path = it.data(Qt.UserRole) if it else None
        return [path] if path else []

    def startDrag(self, _actions):
        paths = self.paths()
        if paths:
            _drag(self, MOVE_MIME, paths)

    def dragEnterEvent(self, e):
        if self._ours(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._ours(e.mimeData()) and self.indexAt(e.position().toPoint()).isValid():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        md = e.mimeData()
        idx = self.indexAt(e.position().toPoint())
        if not self._ours(md) or not idx.isValid():
            e.ignore()
            return
        e.acceptProposedAction()
        paths = _dragged(md, MIME) or _dragged(md, MOVE_MIME)
        it = self.item(idx.row(), 0)
        self.sheet.match(paths, it.data(Qt.UserRole) if it else idx.row())


# --------------------------------------------------------------------------
# the sheet
# --------------------------------------------------------------------------
class ManualMatchSheet(QDialog):
    FILE_COLS = (N_("File"), N_("Title"), N_("Length"), N_("Track"))
    TRACK_COLS = ("#", N_("Title"), N_("Length"), N_("File"), N_("Difference"))
    ACTION = N_("Use These Matches")
    STATUS_NAME = N_("Match status")

    def __init__(self, page, album, ref=None):
        super().__init__(sheets._window(page))
        sheets.prepare(self, tr("Match Files Manually"))
        self.page, self.album = page, album
        self.profile = getattr(page, "profile", None)     # (for the search sheet opened from here)
        self.worker = None
        self.loading = False                              # (a lookup running: the matches cannot be used)
        self.files, self.tracks, self.pairs = [], [], {}
        self.release = None
        self.ref = ref or planned_ref(album)
        self.setMinimumSize(720, 480)
        self.resize(1040, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.SPACE["m"])
        lay.setSpacing(style.SPACE["s"] + 4)
        lay.addLayout(heading(tr("Match Files Manually: {name}").format(name=Path(album['source_dir']).name),
                              tr(HINT)))
        # the release: the plan's, one chosen, or a link
        bar = QHBoxLayout()
        bar.setSpacing(style.SPACE["s"])
        self.release_label = QLabel("")
        self.release_label.setProperty("role", "headline")
        self.release_label.setAccessibleName(tr("Release"))
        bar.addWidget(self.release_label, 1)
        self.link = QLineEdit()
        self.link.setPlaceholderText(tr("Paste a MusicBrainz or Discogs release link"))
        self.link.setAccessibleName(tr("Release link"))
        self.link.setClearButtonEnabled(True)
        self.link.returnPressed.connect(self.use_link)
        bar.addWidget(self.link, 1)
        self.choose_btn = QPushButton(tr("Choose Release…"))
        self.choose_btn.setIcon(icons.icon("search", "ink"))
        self.choose_btn.setAccessibleName(tr("Choose another release"))
        self.choose_btn.setAutoDefault(False)
        self.choose_btn.clicked.connect(self.choose_release)
        bar.addWidget(self.choose_btn)
        lay.addLayout(bar)
        self.link_note = QLabel(tr("Return sends the release ID in the link to MusicBrainz (or to Discogs, with "
                                   "your Discogs token); nothing else."))
        self.link_note.setProperty("role", "caption")
        self.link_note.setWordWrap(True)
        lay.addWidget(self.link_note)
        # the two panes
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.file_table = FileTable(self)
        self.track_table = TrackTable(self)
        for t, cols, name in ((self.file_table, self.FILE_COLS, tr("Your files")),
                              (self.track_table, self.TRACK_COLS, tr("The release's tracks"))):
            _setup(t, cols, name)
            t.itemSelectionChanged.connect(self.update_buttons)
            if t is self.track_table:                     # (File: capped, or left out when the pane is narrow)
                t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            cap = QLabel(name)
            cap.setProperty("role", "headline")
            v.addWidget(cap)
            v.addWidget(t, 1)
            split.addWidget(box)
        split.setSizes([500, 540])
        lay.addWidget(split, 1)
        # what is left, and the tools
        tools = QHBoxLayout()
        tools.setSpacing(style.SPACE["s"])
        self.match_btn = self._tool(tr("&Match"), "arrow-right", tr("Match the selected files to the selected track"),
                                    lambda: self.match(self.file_table.paths(), self.current_track()))
        self.unmatch_btn = self._tool(tr("&Unmatch"), "x", tr("Take the selected pairs apart"), self.unmatch)
        self.auto_btn = self._tool(tr("&Auto Match"), "sparkles", tr("Match again by track number, title and length"),
                                   self.auto)
        for b in (self.match_btn, self.unmatch_btn, self.auto_btn):
            tools.addWidget(b)
        tools.addStretch(1)
        lay.addLayout(tools)
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        self.status.setAccessibleDescription(tr(self.STATUS_NAME))
        lay.addWidget(self.status)
        row, self.ok = sheets.button_row(self, tr(self.ACTION), self.use)
        lay.addLayout(row)
        for t in (self.file_table, self.track_table):     # (Delete or Backspace in a pane: Unmatch)
            for key in (QKeySequence(QKeySequence.Delete), QKeySequence(Qt.Key_Backspace)):
                sc = QShortcut(key, t)
                sc.setContext(Qt.WidgetShortcut)
                sc.activated.connect(self.unmatch)
        self.update_buttons()
        self.load()

    def keyPressEvent(self, e):
        # (Return in the link field reads the link; it never uses the matches by surprise)
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(self.focusWidget(), QLineEdit):
            return
        super().keyPressEvent(e)

    def _tool(self, text, icon, tip, fn):
        b = QPushButton(text)
        b.setIcon(icons.icon(icon, "ink"))
        b.setProperty("role", "tool")
        b.setToolTip(tip)
        b.setAccessibleName(text.replace("&", ""))
        b.setAutoDefault(False)
        b.clicked.connect(fn)
        return b

    # ---- page side --------------------------------------------------------
    @property
    def running(self):                                 # (the search sheet asks its page)
        return bool(getattr(self.page, "running", False))

    def _profile_name(self):
        box = getattr(self.page, "profile", None)
        return box.currentText() if box is not None and hasattr(box, "currentText") else "default"

    def say(self, text):
        say(self.status, tr(self.STATUS_NAME), text)

    # ---- loading ----------------------------------------------------------
    def run(self, fn, done, busy_text):
        self.stop()
        self.say(busy_text)
        me = None

        def current():                                 # (an older run, answered late, is dropped)
            return me is not None and me is self.worker
        me = self.worker = start(self, fn, lambda r: current() and r is not None and done(r),
                                 failed=lambda msg: current() and self.failed(msg))

    def stop(self):
        if self.worker is not None:
            self.worker.cancelled = True

    @staticmethod
    def failed_words(msg) -> str:
        first = (msg or "").strip().splitlines()[0] if (msg or "").strip() else tr("the lookup did not finish")
        if not first.endswith((".", "!", "?")):
            first += "."
        return tr("{first} Choose another release, or try again.").format(first=first)

    def failed(self, msg):
        """The lookup failed: the release shown before (and its pairs) stays the one matched to."""
        self.loading = False
        self.say(self.failed_words(msg))
        self.update_buttons()

    def load(self, ref=None):
        """The files (and the release's tracks, when there is one) read on a worker. The release
        becomes the sheet's (self.ref) only once its tracks are read (loaded)."""
        album, ref, profile = self.album, (self.ref if ref is None else ref), self._profile_name()

        def work(w):
            files = None if self.files else file_rows(album)
            if w.cancelled:
                return None
            rel = None
            if ref:
                from . import versions
                from .common import load_profile
                try:                                   # (a lookup that fails keeps the files read)
                    lk = versions.make_lookup(profile)
                    try:
                        try:
                            prof = load_profile(profile)
                        except Exception:
                            prof = None
                        rel = release_tracks(lk, ref, prof)
                    finally:
                        lk.close()
                except Exception as e:
                    return {"files": files, "release": None, "ref": None, "error": str(e) or type(e).__name__}
            return {"files": files, "release": rel, "ref": ref}
        self.loading = True
        self.run(work, self.loaded,
                 tr("Reading the files and the release's tracks…") if ref else tr("Reading the files…"))
        self.update_buttons()

    def loaded(self, res):
        self.loading = False
        if res.get("files") is not None:
            self.files = res["files"]
        if "error" in res:                             # (the release shown before, and its pairs, stay)
            self.fill()
            self.say(self.failed_words(res["error"]))
            return
        self.ref = res.get("ref") or ""
        self.release = res.get("release")
        self.tracks = (self.release or {}).get("tracks") or []
        saved = (self._edit() or {}).get(MATCH_KEY) or {}
        if self.release and same_ref(saved.get("ref"), self.ref):
            mine = {f["path"] for f in self.files}
            self.pairs = {k: int(v) for k, v in (saved.get("pairs") or {}).items()
                          if k in mine and 0 <= int(v) < len(self.tracks)}
        elif self.release and same_ref(self.ref, planned_ref(self.album)):
            self.pairs = auto_match(self.files, self.tracks, planned_pairs(self.files, self.tracks))
        else:
            self.pairs = auto_match(self.files, self.tracks) if self.tracks else {}
        self.fill()

    def _edit(self):
        insp = getattr(getattr(self.page, "win", None), "inspector", None)
        if insp is None or not hasattr(insp, "edit_of"):
            return None
        return insp.edit_of(self.album)

    # ---- choosing the release ---------------------------------------------
    def use_link(self):
        import engine
        text = self.link.text().strip()
        if not text:
            return
        if engine.parse_release_ref(text)[0] is None:
            self.say(tr("That is not a MusicBrainz or Discogs release link."))
            return
        self.link.clear()
        self.choose(text)

    def choose_release(self):
        """The search sheet, its choice handed back here (nothing is planned until you use the matches)."""
        dlg = PickReleaseSheet(self, self.album, self.choose)
        self._pick = dlg
        dlg.open()
        return dlg

    def choose(self, ref):
        """Another release's tracks on the right (its pairs made afresh once they are read)."""
        self.load(ref)

    # ---- pairs ------------------------------------------------------------
    def current_track(self):
        rows = sorted({i.row() for i in self.track_table.selectedIndexes()})
        if not rows:
            return None
        it = self.track_table.item(rows[0], 0)
        return it.data(Qt.UserRole) if it else None

    def match(self, paths, track):
        """paths matched to track and the tracks after it, in order (a file or a track that was
        in another pair leaves it)."""
        if track is None or not self.tracks:
            return
        mine = {f["path"] for f in self.files}
        paths = [p for p in paths or [] if p in mine]
        for n, path in enumerate(paths):
            j = track + n
            if j >= len(self.tracks):
                break
            for k in [k for k, v in self.pairs.items() if v == j]:
                del self.pairs[k]
            self.pairs[path] = j
        self.fill()

    def unmatch(self):
        """The selected pairs taken apart: the selected files' in the files pane, the selected
        track's in the tracks pane, both from the button."""
        paths = set(self.file_table.paths()) if not self.track_table.hasFocus() else set()
        track = self.current_track() if not self.file_table.hasFocus() else None
        tracks = {track} if track is not None else set()
        before = len(self.pairs)
        self.pairs = {k: v for k, v in self.pairs.items() if k not in paths and v not in tracks}
        if len(self.pairs) != before:
            self.fill()

    def unmatch_paths(self, paths):
        """These files' pairs taken apart (a track's file dragged back to the files)."""
        gone = set(paths or [])
        if any(p in self.pairs for p in gone):
            self.pairs = {k: v for k, v in self.pairs.items() if k not in gone}
            self.fill()

    def auto(self):
        """Matched again from the start: the plan's own pairs first when the release is the plan's."""
        keep = planned_pairs(self.files, self.tracks) if same_ref(self.ref, planned_ref(self.album)) else None
        self.pairs = auto_match(self.files, self.tracks, keep)
        self.fill()

    def add_files(self, paths):
        """Files dropped on the left pane: only the album's own files can be matched (another
        folder is previewed by dropping it on the window)."""
        mine = {os.path.realpath(f["path"]) for f in self.files}
        others = [p for p in paths if os.path.realpath(p) not in mine]
        if others:
            self.say(tr("{0} {1} not part of this album's preview: "
                        "drop the folder on the main window to preview it.").format(_names(others),
                                                                                 'is' if len(others) == 1 else 'are'))
        else:
            self.say(tr("Those files are already listed."))

    # ---- showing ----------------------------------------------------------
    def _cell(self, text, data=None, muted=False):
        it = QTableWidgetItem(text)
        if data is not None:
            it.setData(Qt.UserRole, data)
        if muted:
            it.setForeground(style.status_color("ignored"))
        return it

    def _missing(self, word):
        from .common import status_cell
        return status_cell(QTableWidgetItem(), "unresolved", word)

    def fill(self):
        by_track = {v: k for k, v in self.pairs.items()}
        by_path = {f["path"]: f for f in self.files}
        sel_files, sel_track = set(self.file_table.paths()), self.current_track()
        ft = self.file_table
        ft.setRowCount(len(self.files))
        for r, f in enumerate(self.files):
            ft.setItem(r, 0, self._cell(f["name"], f["path"]))
            ft.item(r, 0).setToolTip(f["path"])
            ft.setItem(r, 1, self._cell(f["title"]))
            ft.setItem(r, 2, self._cell(clock(f["secs"])))
            j = self.pairs.get(f["path"])
            ft.setItem(r, 3, self._cell(self._track_name(j)) if j is not None else self._missing(tr("Not matched")))
        tt = self.track_table
        tt.setRowCount(len(self.tracks))
        multi = len({t["disc"] for t in self.tracks}) > 1
        for r, t in enumerate(self.tracks):
            tt.setItem(r, 0, self._cell(f"{t['disc']}-{t['number']}" if multi else str(t["number"]), r))
            tt.setItem(r, 1, self._cell(t["title"] + (f" — {t['artist']}" if t.get("artist") and
                                                     t["artist"] != (self.release or {}).get("artist") else "")))
            tt.setItem(r, 2, self._cell(clock(t["secs"])))
            path = by_track.get(r)
            if path is None:
                tt.setItem(r, 3, self._missing(tr("No file")))
                tt.setItem(r, 4, self._cell(""))
                continue
            tt.setItem(r, 3, self._cell(by_path[path]["name"], path))      # (what a drag off the track takes)
            d, ok = length_diff(by_path[path], t)
            cell = self._cell(diff_words(d) if d is not None else tr("length unknown"), muted=d is None)
            if not ok:                                    # (short in the cell, the reason in its tooltip)
                from .common import status_cell
                words = tr("{0} longer") if d > 0 else tr("{0} shorter")
                status_cell(cell, "unresolved", words.format(diff_words(d)), "triangle-alert")
                cell.setToolTip((tr("The file is {0} longer than the track: more than the tolerance") if d > 0 else
                                 tr("The file is {0} shorter than the track: more than the tolerance"))
                                .format(clock(abs(d))))
            tt.setItem(r, 4, cell)
        from PySide6.QtCore import QItemSelectionModel
        ft.clearSelection()                               # (the selection kept across a refill)
        for r, f in enumerate(self.files):
            if f["path"] in sel_files:
                ft.selectionModel().select(ft.model().index(r, 0),
                                           QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if sel_track is not None and sel_track < tt.rowCount():
            tt.selectRow(sel_track)
        rel = self.release
        self.release_label.setText(f"{rel['title']} — {rel['artist']}" if rel and rel.get("artist") else
                                   (rel or {}).get("title") or tr("No release chosen yet"))
        self.say(self.counts())
        tt.fit()
        self.update_buttons()

    def _track_name(self, j):
        t = self.tracks[j]
        multi = len({x["disc"] for x in self.tracks}) > 1
        return (f"{t['disc']}-{t['number']}" if multi else str(t["number"])) + f". {t['title']}"

    def counts(self) -> str:
        if not self.tracks:
            return (tr("Choose the release these files are from (Choose Release… or paste a link).")
                    if self.files else tr("This album has no files to match."))
        nf, nt, np_ = len(self.files), len(self.tracks), len(self.pairs)
        parts = [tr("{np_} of {0} matched").format(plural(nf, tr("file"), tr("files")), np_=np_)]
        if nf - np_:
            parts.append(tr("{0} not matched (left with their own tags)").format(plural(nf - np_, tr("file"),
                                                                                         tr("files"))))
        if nt - np_:
            parts.append(tr("{0} without a file").format(plural(nt - np_, tr("track"), tr("tracks"))))
        far = sum(1 for k, v in self.pairs.items() if not length_diff(
            next(f for f in self.files if f["path"] == k), self.tracks[v])[1])
        if far:
            parts.append(tr("{0} differ in length").format(plural(far, tr("pair"), tr("pairs"))))
        why = self.blocked()
        return "; ".join(parts) + "." + (f" {why}" if why else "")

    def blocked(self) -> str:
        """Why the matches cannot be used ("" when they can)."""
        if self.loading:
            return tr("Still reading the release's tracks.")
        if not self.tracks:
            return tr("No release's tracks to match to.")
        if renames(self.album) and not ENGINE_RENAMES_BY_MATCH:
            planned = planned_pairs(self.files, self.tracks) if same_ref(self.ref, planned_ref(self.album)) else {}
            if self.pairs != planned or any(f["path"] not in self.pairs for f in self.files):
                return (tr("The plan renames or moves these files after the tracks it gave them, and would still "
                           "do so whatever you match here, so a file's name would not fit its tags. For now this "
                           "album can be matched only as the plan paired it (Auto Match puts that back); turn off "
                           "renaming and moving in the profile to match the files differently."))
        if not same_ref(self.ref, planned_ref(self.album)) and not ENGINE_TAKES_MATCHES:
            if len(self.tracks) != album_file_count(self.album):
                return (tr("This release has {0} tracks and the album {1} "
                           "files: only a release with as many tracks as files can be chosen here for now.")
                        .format(len(self.tracks), album_file_count(self.album)))
            if any(f["path"] not in self.pairs for f in self.files):
                return (tr("Match every file to use this release: with another release than the plan's, a file "
                           "left unmatched would still be given the track in its place for now."))
        return ""

    def update_buttons(self):
        has_track = self.current_track() is not None
        self.match_btn.setEnabled(bool(self.file_table.paths()) and has_track)
        sel_paths = set(self.file_table.paths())
        self.unmatch_btn.setEnabled(any(p in self.pairs for p in sel_paths) or
                                    (has_track and self.current_track() in self.pairs.values()))
        self.auto_btn.setEnabled(bool(self.files and self.tracks))
        why = self.blocked()
        self.ok.setEnabled(not why)
        self.ok.setToolTip(why)

    # ---- the result -------------------------------------------------------
    def use(self):
        """The pairs as your edits of the album, then the album previewed again (with the
        release chosen, when it is not the plan's)."""
        if self.blocked():
            self.say(self.blocked())
            return
        if self.running:
            self.say(tr(BUSY))
            return
        win = getattr(self.page, "win", None)
        insp = getattr(win, "inspector", None)
        if insp is None:
            self.say(tr("The inspector is not ready: preview the folder first."))
            return
        insp.remember()                               # (Edit > Undo Edit takes the matches back)
        old = insp.edit_of(self.album, create=True)
        new = as_edit(self.album, self.files, self.tracks, self.pairs, self.ref, old)
        other = not same_ref(self.ref, planned_ref(self.album))
        if other:                                     # (the release chosen before, for an undo that restores it)
            key = os.path.realpath(self.album["source_dir"])
            new[MATCH_KEY]["was_forced"] = copy.deepcopy((getattr(self.page, "forced", None) or {}).get(key))
        old.clear()
        old.update(new)
        if other:
            from .search import force_release
            if not force_release(self.page, self.album, self.ref):
                self.say(tr("The release could not be chosen for this album."))
                return
            if win is not None and hasattr(win, "statusBar"):
                win.statusBar().showMessage(
                    tr("Previewing {name} again with the release you chose and your "
                       "matches; nothing is written until you apply. Edit > Undo Edit takes the matches back, but the "
                       "release stays chosen: to go back, choose another release (Album > Change Release).")
                                            .format(name=Path(self.album['source_dir']).name), 10000)
        else:
            self.page.preview()
            if win is not None and hasattr(win, "statusBar"):
                win.statusBar().showMessage(
                    tr("Previewing {name} again with your matches; nothing is written "
                       "until you apply. Edit > Undo Edit takes them back.")
                                            .format(name=Path(self.album['source_dir']).name), 10000)
        self.stop()
        self.accept()

    def done(self, r):
        super().done(r)
        self.stop()


def _pick_sheet():
    from .search import SearchSheet

    class Pick(SearchSheet):
        """gui.search's sheet, its "use" handing the release back to the match sheet."""
        ACTION = N_("Match to This Release")

        def __init__(self, parent, album, chosen):
            self._chosen = chosen
            super().__init__(parent, album)
            hint = self.head.itemAt(1).widget() if self.head.count() > 1 else None
            if hint is not None:
                hint.setText(tr("Find the release these files are from; its tracks replace the right-hand list. "
                                "Nothing is planned until you use the matches."))

        def use(self):
            row = self.current()
            if hasattr(self, "kinds") and self.kind() == "artist":
                return super().use()                   # (an artist: their releases listed)
            if not self.can_use(row):
                return
            self.stop()
            self.accept()
            self._chosen(row["ref"])
    return Pick


def PickReleaseSheet(parent, album, chosen):
    """The search sheet (gui.search.SearchSheet) as a picker: chosen(ref) on use."""
    return _pick_sheet()(parent, album, chosen)
