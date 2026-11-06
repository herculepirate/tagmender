#!/usr/bin/env python3
"""tools/gui_screens.py - Screenshots of every page, the inspector, Preferences, a sheet and the
menus, and the M7 sheets (search, other versions, your pressing, a tag edited in the inspector,
the script editor, tags from file names, file info, the log, a job's details), and the M8 views
(Preferences > Account, Match Files Manually, Look Up CD, a release a browser asked for, your
collections, the ratings and AcoustID confirmations, the inspector's rating row, the player bar,
the Controls menu, and the states between: Look Up CD looking up and failed, no collections, a failed
AcoustID send, Match Files Manually with no release), the run report, in light and dark, at 1280x800 and
960x600: for checking the windows against the app's design rules (Apple HIG adapted to Qt).

    python3 tools/gui_screens.py ROUND [--out ~/Downloads/tagmender_m7_screens] [--only overview,sheet]
    python3 tools/gui_screens.py r0 --out ~/Downloads/tagmender_m8_screens --only m8-
    python3 tools/gui_screens.py readme --readme     # the README's pictures, 1440x900 at 2x, into docs/screenshots

Writes <out>/<ROUND>/<theme>-<w>x<h>-<nn>-<name>.png and manifest.txt (file, page, theme, size,
sha1). Safe by construction: the offscreen platform, fresh temporary XDG folders (config, state,
cache) removed at the end, no tagger settings, no network (sockets refuse to connect), and the engine is
never called - the window gets a made-up plan through its normal planned() path, and the jobs,
history and watcher lists are fixed rows patched in this process only; so are the search sheets'
lookups (gui.versions: made-up releases), the job's details, a release's tracks for Match Files
Manually, the CD's releases, and no MusicBrainz login is made (the account sheets get a made-up
name). File Info reads a small WAV the
tool writes in its temporary folder; the log viewer a log written there. Refuses an output folder
under /media. Exits 1 when a shot failed (the others are still taken)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SHOTS = ("overview-empty", "overview", "inspector-evidence", "inspector-tags", "inspector-artwork",
         "needs-attention", "history", "watched-folders", "jobs", "profiles", "preferences-general",
         "preferences-keys", "preferences-checks", "sheet-apply", "menu-file", "menu-album",
         "search", "other-versions", "choose-pressing", "inspector-tags-edited", "script-editor",
         "tags-from-filenames", "file-info", "log", "job-details", "menu-tools", "menu-album-context",
         "m8-preferences-account", "m8-preferences-account-lower", "m8-manual-match", "m8-cd-lookup", "m8-browser-release", "m8-collections",
         "m8-ratings", "m8-acoustid", "m8-inspector-rating", "m8-player", "m8-menu-controls", "m8-menu-album",
         "m8-cd-lookup-busy", "m8-cd-lookup-error", "m8-collections-empty", "m8-acoustid-error",
         "m8-manual-match-empty", "setup", "setup-welcome", "sheet-missing-key", "report")
SIZES = ((1280, 800), (960, 600))
THEMES = ("light", "dark")
# The README's pictures: file in docs/screenshots <- (theme, shot name)
README_SHOTS = {"overview-evidence.png": ("light", "inspector-evidence"),
                "inspector-tags-dark.png": ("dark", "inspector-tags"),
                "choose-pressing.png": ("light", "choose-pressing"),
                "report.png": ("light", "report"),
                "manual-match.png": ("light", "m8-manual-match"),
                "profile-editor.png": ("light", "profiles-names"),
                "preferences-network-dark.png": ("dark", "preferences-network")}
TIDY = {"on": False}       # README look: albums by name, ~ for the made-up home, a tested connection


def isolate(scale=1):
    """Before Qt or any TagMender module loads: temporary folders, offscreen, no network."""
    tmp = Path(tempfile.mkdtemp(prefix="tm-screens-"))
    if scale != 1:
        os.environ["QT_SCALE_FACTOR"] = str(scale)    # (sharp pictures for high-density screens)
    for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        (tmp / k).mkdir()
        os.environ[k] = str(tmp / k)
    sys.path.insert(0, str(REPO))
    import credentials                                  # (no key of the person's own: Setup shows "Not set")
    for var in credentials.ENV.values():
        os.environ.pop(var, None)
    # (a screen large enough for the biggest window at this scale: Qt's offscreen screen is
    # otherwise 800x800, and a window larger than its screen is cut to it)
    screen = tmp / "offscreen.json"
    screen.write_text(json.dumps({"screens": [{"name": "shots", "x": 0, "y": 0, "width": 4000, "height": 3000,
                                               "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}))
    os.environ.update(QT_QPA_PLATFORM=f"offscreen:configfile={screen}", TAGMENDER_TAGGER_INI="/nonexistent",
                      PYTHONDONTWRITEBYTECODE="1", QT_ACCESSIBILITY="1")
    sys.dont_write_bytecode = True

    def refuse(*_a, **_k):
        raise OSError("no network in the screenshot tool")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    return tmp


# --------------------------------------------------------------------------
# sample data
# --------------------------------------------------------------------------
def _ident(status="identified", provider="musicbrainz", conf=None, margin=None, tier="exact", why=(), evidence=(),
           codes=(), reason="", release_id=""):
    return {"status": status, "provider": provider, "release_id": release_id,
            "url": f"https://musicbrainz.org/release/{release_id}" if release_id else "",
            "confidence": conf, "margin": margin, "why": list(why), "evidence": list(evidence),
            "reason_codes": list(codes), "provenance": {"album": "MusicBrainz", "artist": "MusicBrainz",
                                                        "date": "MusicBrainz", "label": "Discogs",
                                                        "catalognumber": "Discogs", "genre": "Discogs"}
            if status == "identified" else {}, "provider_errors": {}, "tier": tier, "reason": reason}


def _album(root, artist, title, status, ident, files=0, diff=(), **extra):
    d = root / artist / title
    d.mkdir(parents=True, exist_ok=True)
    def own(i):                                       # (each file's own number and title, as a plan has them)
        rows = [dict(r) for r in diff if r["field"] not in ("title", "tracknumber")]
        if diff:
            rows += [{"field": "title", "before": [f"Track {i + 1}"], "after": [f"Track {i + 1}"],
                      "action": "preserved"},
                     {"field": "tracknumber", "before": [str(i + 1)], "after": [str(i + 1)], "action": "preserved"},
                     {"field": "totaltracks", "before": [], "after": [str(files)], "action": "added"}]
        return rows
    fs = [{"src": str(d / f"{i + 1:02d} Track {i + 1}.flac"), "dst": str(d / f"{i + 1:02d} Track {i + 1}.flac"),
           "op": "tag" if diff else "none", "changes": {}, "diff": own(i)} for i in range(files)]
    a = {"source_dir": str(d), "status": status, "label": "", "rule": None, "identification": ident,
         "release": {"albumartist": [artist], "album": [title]}, "files": fs, "companions": [], "artwork": None,
         "conflicts": [], "skipped": [], "notes": [], "album_files": [f["src"] for f in fs], "sidecars": []}
    a.update(extra)
    return a


def covers(tmp):
    """Two flat covers painted with QPainter: the album's own (small) and the new one."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    out = []
    for name, size, bg, fg, text in (("old.jpg", 300, "#8B6F4E", "#F3E9DC", "A"), ("new.png", 1200, "#1F4E5F",
                                                                                     "#F6C85F", "Ω")):
        img = QImage(size, size, QImage.Format_RGB32)
        img.fill(QColor(bg))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fg))
        p.drawEllipse(QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6))
        p.setPen(QColor(bg))
        f = QFont()
        f.setPixelSize(int(size * 0.3))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
        p.end()
        img.save(str(tmp / name))
        out.append(tmp / name)
    return out


def sample_plan(tmp) -> dict:
    lib = tmp / "Music"
    old, new = covers(tmp)
    diff = [{"field": "album", "before": ["Northern Lights (Remaster)"], "after": ["Northern Lights"],
             "action": "changed"},
            {"field": "date", "before": [], "after": ["1998-03-02"], "action": "added"},
            {"field": "label", "before": [], "after": ["Polar Records"], "action": "added"},
            {"field": "catalognumber", "before": [], "after": ["POL 113"], "action": "added"},
            {"field": "comment", "before": ["ripped 2004"], "after": [], "action": "removed"},
            {"field": "artist", "before": ["Aurora Vale"], "after": ["Aurora Vale"], "action": "preserved"},
            {"field": "title", "before": ["Track 1"], "after": ["Track 1"], "action": "preserved"}]
    mb = "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c"
    ready = _album(lib, "Aurora Vale", "Northern Lights", "ready",
                   _ident(conf=97.4, margin=21.0, release_id=mb,
                          evidence=["track count 11 = 11", "track lengths agree (±2 s)", "barcode 5012345678900",
                                    "catalogue number POL 113"], why=[]),
                   files=11, diff=diff)
    shutil.copy(old, ready["source_dir"] + "/cover.jpg")
    ready["artwork"] = {"front": str(new), "embed": str(new), "source": "Cover Art Archive", "why": "",
                        "embed_into": [f["src"] for f in ready["files"]], "file": ready["source_dir"] + "/cover.jpg",
                        "embed_replace": [], "replaced_file": None, "width": 1200, "height": 1200}
    albums = [
        ready,
        _album(lib, "Glass Harbour", "Low Tide", "ready",
               _ident(conf=91.2, margin=6.5, tier="partial", release_id="9d1e2f3a-4b5c-4d6e-8f70-1a2b3c4d5e6f",
                      why=["two pressings of 2009 fit"], evidence=["track count 9 = 9"],
                      reason="ambiguous_release: album known, pressing not established"),
               files=9, diff=diff[:3]),
        _album(lib, "The Quiet Rooms", "Hallway", "ready",
               _ident(conf=92.0, margin=3.1, tier="album_only", release_id="",
                      reason="ambiguous_release: album known, pressing not established"),
               files=8, diff=diff[:2]),
        _album(lib, "Mira Sol", "Daybreak", "unchanged", _ident(conf=99.0, margin=30.0, release_id=mb), files=10),
        _album(lib, "Unknown Artist", "CD1", "unresolved",
               _ident(status="unresolved", provider="", conf=None, codes=["no_candidate", "low_similarity"],
                      reason="no_candidate,low_similarity: nothing lists an album of 7 tracks by this name"),
               files=7),
        _album(lib, "Stone Fields", "Live at the Barn [no vox]", "excluded", _ident(status="unresolved"),
               files=6, label="Ignored", rule={"name": "[no vox] editions", "matched": "album"}),
        _album(lib, "Various", "Podcast Episodes", "skipped", _ident(status="unresolved"), files=3,
               label="Skipped", skipped=[{"file": str(lib / "Various" / "Podcast Episodes" / "ep1.m4b"),
                                         "label": "Skipped", "rule": "profile formats", "matched": "m4b"}]),
        _album(lib, "North Pier", "Signals", "blocked", _ident(conf=95.0, margin=12.0, release_id=mb), files=10,
               conflicts=["the destination folder already holds another album"]),
        _album(lib, "Red Kite", "Ascent", "conflict", _ident(conf=96.0, margin=15.0, release_id=mb), files=12,
               diff=diff[:2], conflicts=["03 Track 3.flac changed after the preview"]),
        _album(lib, "Low Orbit", "Satellites", "failed", _ident(conf=94.0, margin=9.0, release_id=mb), files=10,
               diff=diff[:2], conflicts=["could not write 05 Track 5.flac: permission denied"]),
    ]
    summary = {}
    for a in albums:
        summary[a["status"]] = summary.get(a["status"], 0) + 1
    return {"job": "20261031-101500-ab12", "profile": "Library", "operation": "move", "albums": albums,
            "summary": summary, "provider_notes": {},
            "profile_data": {"name": "Library", "unattended": False, "destination": str(tmp / "library"),
                             "thresholds": {"auto_min_confidence": 90.0, "replace_min_confidence": 90.0,
                                            "organize_min_confidence": 95.0}}}


def sample_report_plan(plan) -> dict:
    """The made-up plan with what a report shows: suggested releases, tracks missing, a version
    split, a release without track lengths (all invented)."""
    import copy
    p = copy.deepcopy(plan)
    by = {Path(a["source_dir"]).name: a for a in p["albums"]}
    sugg = lambda *c: [{"source": s, "id": i, "url": (f"https://musicbrainz.org/release/{i}" if s == "musicbrainz"  # noqa: E731
                                                         else f"https://www.discogs.com/release/{i}"),
                        "ref": i if s == "musicbrainz" else f"discogs:{i}", "score": sc, "title": t,
                        "lengths": ln} for s, i, sc, t, ln in c]
    by["Low Tide"]["identification"]["candidates"] = sugg(
        ("musicbrainz", "9d1e2f3a-4b5c-4d6e-8f70-1a2b3c4d5e6f", 91.2, "Glass Harbour - Low Tide (2009)", None),
        ("discogs", "3141592", 90.8, "Glass Harbour - Low Tide (2009)", None))
    by["Hallway"]["identification"]["candidates"] = sugg(
        ("discogs", "2718281", 92.0, "The Quiet Rooms - Hallway (2012)", False))
    by["Hallway"]["identification"]["why"] = ["durations unavailable (barcode decides)"]
    by["CD1"]["identification"]["candidates"] = sugg(("discogs", "1618033", 61.0, "Unknown Artist - CD1 (2001)",
                                                      None))
    lights = by["Northern Lights"]
    lights["notes"] = ["tracks 4, 9 of the release are not in this folder: each file is tagged as its own track"]
    return p


def sample_results(plan) -> list:
    words = {"ready": ("done", "Done", ""), "unresolved": ("done", "Unresolved", ""),
             "unchanged": ("unchanged", "Up to date", ""), "excluded": ("excluded", "Ignored", ""),
             "skipped": ("skipped", "Skipped", ""), "blocked": ("blocked", "Blocked", ""),
             "conflict": ("conflict", "Changed", "03 Track 3.flac changed after the preview"),
             "failed": ("failed", "Failed", "could not write 05 Track 5.flac: permission denied")}
    return [(a["source_dir"],) + words.get(a["status"], ("done", "Done", "")) + (0,) for a in plan["albums"]]


def job_time(job) -> float:
    """When a job started, from its ID (local time, as plan.py and logsetup make them)."""
    return time.mktime(time.strptime(job[:15], "%Y%m%d-%H%M%S"))


def fixed_rows(tmp):
    now = job_time("20261031-101500-ab12") + 600              # (one clock: the IDs, the log and the rows agree)
    jobs = [("20261031-101500-ab12", "Library", now - 600, now - 540, "done",
             json.dumps({"done": 8, "unchanged": 1, "failed": 1})),
            ("20261030-201000-cd34", "Inbox", now - 86400, now - 86300, "done", json.dumps({"done": 3})),
            ("20261029-090000-ef56", "Library", now - 2 * 86400, now - 2 * 86400 + 90, "undone",
             json.dumps({"done": 12}))]
    history = [(now - 600, "20261031-101500-ab12", str(tmp / "music/Stone Fields/Live at the Barn [no vox]"),
                "Ignored", "[no vox] editions", ""),
               (now - 3600, "20261031-091000-aa11", str(tmp / "music/Various/Podcast Episodes/ep1.m4b"), "Skipped",
                "profile formats", ""),
               (now - 86400, "20261030-201000-cd34", str(tmp / "inbox/Karaoke Hits"), "Ignored", "karaoke", "")]
    pending = [{"path": tmp / "plans/20261031-110000-gh78.json", "job": "20261031-110000-gh78", "profile": "Inbox",
                "albums": [(str(tmp / "inbox/Harbour Lights - Ferry"), "Ready",
                            str(tmp / "library/Harbour Lights/Ferry"))]},
               {"path": tmp / "plans/20261031-111500-ij90.json", "job": "20261031-111500-ij90", "profile": "Inbox",
                "albums": [(str(tmp / "inbox/Moss - Garden"), "Ready", str(tmp / "library/Moss/Garden"))]}]
    recent = [(str(tmp / "inbox/Old Pines - Bark"), "Inbox", "done", now - 7200, ""),
              (str(tmp / "inbox/Karaoke Hits"), "Inbox", "ignored", now - 86400, "")]
    attention = [{"folder": str(tmp / "music/Unknown Artist/CD1"), "codes": ["no_candidate"],
                  "reason": "nothing lists an album of 7 tracks by this name", "files": 7, "mark": "Unresolved"},
                 {"folder": str(tmp / "music/Glass Harbour/Low Tide"), "codes": ["ambiguous_release"],
                  "reason": "two pressings of 2009 fit", "files": 9, "mark": "Partial"},
                 {"folder": str(tmp / "music/Echo Unit/Rarities"), "codes": ["incomplete_album", "duration_mismatch"],
                  "reason": "14 tracks here, 16 on every candidate", "files": 14, "mark": "Unresolved"}]
    return jobs, history, pending, recent, attention


def profiles(tmp):
    import profiles as pf
    (tmp / "library").mkdir(exist_ok=True)
    (tmp / "inbox").mkdir(exist_ok=True)
    pf.save(pf.from_dict({"name": "Library", "description": "File into the library", "operation": "move",
                          "destination": str(tmp / "library")}))
    pf.save(pf.from_dict({"name": "Inbox", "description": "Watch the inbox", "operation": "move",
                          "destination": str(tmp / "library"),
                          "sources": [{"path": str(tmp / "inbox"), "watch": True}]}))
    pf.save(pf.from_dict({"name": "In place", "description": "Fix tags where they are", "operation": "in_place"}))


def _release(source, rid, title, artist, date, country, label, catno, barcode, fmt, tracks, **extra):
    url = (f"https://musicbrainz.org/release/{rid}" if source == "mb" else f"https://www.discogs.com/release/{rid}")
    return dict({"source": source, "id": rid, "url": url, "ref": rid if source == "mb" else f"discogs:{rid}",
                 "title": title, "artist": artist, "date": date, "country": country, "label": label, "catno": catno,
                 "barcode": barcode, "format": fmt, "tracks": tracks}, **extra)


def releases():
    """Made-up releases for the search and other-versions sheets (no lookup is made)."""
    def scored(r, score, fit, formats=("cd",)):
        return dict(r, score=score, fit=fit, notes=[], n_tracks=r["tracks"], formats=formats)
    found = [_release("mb", "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c", "Hallway", "The Quiet Rooms", "2009-04-20", "GB",
                      "Polar Records", "POL 201", "5012345678917", "CD", 8),
             _release("mb", "5c8d2e1f-9b4e-4a72-8d3b-1e6f7a8b9c0d", "Hallway", "The Quiet Rooms", "2009-05-04", "DE",
                      "Polar Records", "POL 201 DE", "5012345678924", "CD", 8),
             _release("mb", "6d9e3f20-ac5f-4b83-9e4c-2f7a8b9c0d1e", "Hallway", "The Quiet Rooms", "2010", "US",
                      "Northside", "NS-0442", "", "2×Vinyl", 10),
             _release("discogs", "2214567", "Hallway", "The Quiet Rooms", "2009", "UK", "Polar Records", "POL 201",
                      "5 012345 678917", "CD, Album", 8)]
    # (the words gui.versions.fit_words gives: same track count; every length agrees; ...)
    tied = [scored(found[0], 96.5, "same track count; every length agrees; barcode agrees"),
            scored(found[1], 96.1, "same track count; every length agrees")]
    rest = [scored(found[2], 71.0, "10 tracks, you have 8; lengths line up in order", ("vinyl",)),
            scored(found[3], 93.0, "same track count; no lengths listed")]
    return found, {"rows": tied + rest, "notes": [], "n_files": 8, "anchors": {}, "cfg": {"auto_min_margin": 5},
                   "media": "cd"}


def sample_wav(tmp, cover):
    """A one-second WAV with ID3 tags and a front cover, for File Info."""
    import wave
    from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TRCK
    from mutagen.wave import WAVE
    path = tmp / "music" / "01 Track 1.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0\0" * 2 * 44100)
    f = WAVE(str(path))
    f.add_tags()
    for frame in (TIT2(encoding=3, text="Track 1"), TPE1(encoding=3, text="The Quiet Rooms"),
                  TALB(encoding=3, text="Hallway"), TRCK(encoding=3, text="1/8"), TDRC(encoding=3, text="2009")):
        f.tags.add(frame)
    f.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front", data=Path(cover).read_bytes()))
    f.save()
    return path


def sample_log():
    """A short tagmender.log where the log viewer looks for it."""
    import logsetup
    d = logsetup.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    lines = ["2026-10-31 10:15:00,120 INFO     job=20261031-101500-ab12 preview of 10 albums under music",
             "2026-10-31 10:15:02,410 INFO     identified Northern Lights: MusicBrainz, 97.4 (margin 21.0)",
             "2026-10-31 10:15:03,002 WARNING  Hallway: two pressings of 2009 fit; tagged as the album only",
             "2026-10-31 10:15:04,550 INFO     CD1: unresolved (nothing lists an album of 7 tracks by this name)",
             "2026-10-31 10:15:09,880 INFO     apply: 8 albums, 79 files staged and verified",
             "2026-10-31 10:15:10,031 ERROR    could not write 05 Track 5.flac: permission denied",
             "2026-10-31 10:15:10,500 INFO     job 20261031-101500-ab12 done: 8 done, 1 unchanged, 1 failed"]
    (d / "tagmender.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sample_job(tmp):
    lib = tmp / "library" / "Aurora Vale" / "Northern Lights (1998)"
    files = [{"src": str(tmp / f"music/Aurora Vale/Northern Lights/{i:02d} Track {i}.flac"),
              "dst": str(lib / f"{i:02d} Track {i}.flac"), "op": "move",
              "changes": [("album", "Northern Lights (Remaster)", "Northern Lights"), ("date", "", "1998-03-02"),
                          ("label", "", "Polar Records")] if i == 1 else [("date", "", "1998-03-02")]}
             for i in (1, 2, 3)]
    return {"job": "20261031-101500-ab12", "profile": "Library", "started": job_time("20261031-101500-ab12"),
            "status": "done",
            "undoable": True, "why": "",
            "albums": [{"source": str(tmp / "music/Aurora Vale/Northern Lights"), "status": "done", "detail": "",
                        "files": files, "other": ["Cover saved as cover.jpg", f"Folder made: {lib}"]},
                       {"source": str(tmp / "music/Low Orbit/Satellites"), "status": "failed",
                        "detail": "could not write 05 Track 5.flac: permission denied", "files": [], "other": []}],
            "undo": []}


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------
class Shooter:
    def __init__(self, app, out, only):
        self.app, self.out, self.only = app, out, only
        self.manifest, self.failed = [], []

    def settle(self, n=8):
        for _ in range(n):
            self.app.processEvents()
            time.sleep(0.01)
        if TIDY["on"]:
            self.tidy_paths()

    def tidy_paths(self):
        """The temporary folder shown as ~, as a real home folder would be."""
        from PySide6.QtWidgets import QLabel
        tmp = str(Path(os.environ["XDG_CONFIG_HOME"]).parent)
        for top in self.app.topLevelWidgets():
            for lab in [top, *top.findChildren(QLabel)]:
                if isinstance(lab, QLabel) and tmp in lab.text():
                    lab.setText(lab.text().replace(tmp, "~"))
            if top.isWindow() and top.focusWidget() is not None and top.focusWidget().inherits("QAbstractButton"):
                top.focusWidget().clearFocus()            # (no focus ring on a button nobody pressed)
        self.app.processEvents()

    def save(self, px, theme, size, nn, name, page):
        fn = f"{theme}-{size[0]}x{size[1]}-{nn:02d}-{name}.png"
        path = self.out / fn
        if not px.save(str(path)):
            raise OSError(f"could not save {path}")
        sha = hashlib.sha1(path.read_bytes()).hexdigest()
        self.manifest.append((fn, page, theme, f"{size[0]}x{size[1]}", sha))

    def take(self, theme, size, nn, name, page, fn):
        if self.only and not any(o in name for o in self.only):
            return
        try:
            px = fn()
            self.settle()
            px = px() if callable(px) else px
            self.save(px, theme, size, nn, name, page)
        except Exception:
            self.failed.append(f"{theme}-{size} {name}: {traceback.format_exc(limit=4)}")


def run(out, only):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication
    tmp = Path(os.environ["XDG_CONFIG_HOME"]).parent
    from PySide6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(lambda _t, _c, msg: None if "This plugin does not support" in msg
                           else sys.stderr.write(msg + "\n"))
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    import executor
    import exclusions as ex
    import watcher
    import gui
    from gui import common, sheets, style, versions
    from gui.dialogs import logs as logs_dialog
    profiles(tmp)
    plan = sample_plan(tmp)
    found, listed = releases()
    sample_log()
    wav = sample_wav(tmp, tmp / "old.jpg")
    lookup = mock.Mock(cfg={"auto_min_margin": 5})
    jobs, history, pending, recent, attention = fixed_rows(tmp)
    common._set_gui_pref("reduce_motion", True)            # (no fades in a screenshot)
    patches = [mock.patch.object(executor, "jobs", lambda limit=50: jobs[:limit]),
               mock.patch.object(executor, "pending_jobs", lambda: []),
               mock.patch.object(executor, "busy_jobs", lambda but=None: []),
               mock.patch.object(executor, "job_albums", lambda job: [("x", "done", "", "", 0)] * 10),
               mock.patch.object(ex.State, "history", lambda self, limit=200: history[:limit]),
               mock.patch.object(watcher, "pending_plans", lambda: pending),
               mock.patch.object(watcher, "recent", lambda limit=50: recent[:limit]),
               mock.patch.object(watcher, "service_supported", lambda: False),
               mock.patch("engine.Engine", side_effect=RuntimeError("the screenshot tool never runs the engine")),
               mock.patch.object(versions, "make_lookup", lambda *_a: lookup),
               mock.patch.object(versions, "search", lambda _lk, kind, text: {"rows": found if kind != "discogs"
                                                                               else found[3:]}),
               mock.patch.object(versions, "versions", lambda *_a, **_k: dict(listed, rows=list(listed["rows"]))),
               mock.patch.object(logs_dialog, "job_details", lambda job: sample_job(tmp))]
    for p in patches:
        p.start()
    shooter = Shooter(app, out, only)
    try:
        for theme in THEMES:
            style.apply(app, theme)
            for size in SIZES:
                shoot_all(shooter, gui, sheets, plan, attention, theme, size, QPoint, QColor, QPainter, Qt)
                shoot_m7(shooter, gui, plan, wav, theme, size, QPoint, QColor, QPainter, Qt)
                shoot_m8(shooter, gui, plan, found, wav, theme, size, QPoint, QColor, QPainter, Qt)
    finally:
        for p in patches:
            p.stop()
    with open(out / "manifest.txt", "w") as f:
        f.write("file\tpage\ttheme\tsize\tsha1\n")
        for row in shooter.manifest:
            f.write("\t".join(row) + "\n")
    return shooter


def shoot_all(sh, gui, sheets, plan, attention, theme, size, QPoint, QColor, QPainter, Qt):
    import copy
    win = gui.Main()
    win.show()
    win.resize(*size)                                   # (after show: the window first fits itself, 1180x760)
    win.inspector_wanted = True                         # (wanted: it steps aside on the empty Overview)
    win.place_inspector()
    sh.settle(20)
    ov = win.pages["Overview"]
    win.go("Overview")
    sh.take(theme, size, 1, "overview-empty", "Overview", lambda: win.grab)
    ov.path = str(Path(plan["albums"][0]["source_dir"]).parent.parent)
    ov.where.setText(ov.path)
    ov.views.setCurrentWidget(ov.results)
    ov.profile.setCurrentText("Library")
    ov.planned(copy.deepcopy(plan))
    if TIDY["on"]:
        ov.table.sortByColumn(1, Qt.AscendingOrder)
    ready = next(r for r in range(ov.proxy.rowCount())
                 if ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)["status"] == "ready"
                 and (not TIDY["on"] or "Northern Lights" in ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)["source_dir"]))
    ov.table.selectRow(ready)
    sh.settle(10)
    sh.take(theme, size, 2, "overview", "Overview", lambda: win.grab)

    def tab(name):
        win.inspector.show_tab(name)
        if name == "Artwork":
            end = time.time() + 5
            while time.time() < end and any(gui._alive(t) for t, _w in getattr(win.inspector, "_threads", [])):
                sh.settle(2)
        if name == "Evidence":
            win.inspector.evidence.more.setChecked(False)
        return win.grab
    for nn, name in ((3, "Evidence"), (4, "Tags"), (5, "Artwork")):
        sh.take(theme, size, nn, f"inspector-{name.lower()}", f"Inspector {name}", lambda n=name: tab(n))
    win.inspector.show_tab("Tags")
    att = win.pages["Needs attention"]
    att.root.setText(str(Path(attention[0]["folder"]).parent.parent))
    att.show_rows(attention)
    for nn, page, name in ((6, "Needs attention", "needs-attention"), (7, "History", "history"),
                           (8, "Watched folders", "watched-folders"), (9, "Jobs", "jobs"),
                           (10, "Profiles", "profiles")):
        def go(p=page):
            win.go(p)
            if p == "Needs attention":
                att.table.selectRow(0)
            if p == "Jobs":
                win.pages["Jobs"].reload()
                win.pages["Jobs"].table.selectRow(0)
            if p == "Profiles":
                pr = win.pages["Profiles"]
                pr.reload()
                pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Library"))
            return win.grab
        sh.take(theme, size, nn, name, page, go)

    def profile_part(key, search=""):
        """The Profile editor scrolled to one row (or with the search field holding words)."""
        win.go("Profiles")
        pr = win.pages["Profiles"]
        pr.reload()
        pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Library"))
        win.search_box.setText(search)
        sh.settle(4)
        row = pr.editor.rows[key]
        pr.editor.verticalScrollBar().setValue(max(0, row.mapTo(pr.editor.widget(), row.rect().topLeft()).y()
                                                   - (34 if TIDY["on"] else 60)))
        return win.grab
    sh.take(theme, size, 30, "profiles-names", "Profiles, names and the example",
            lambda: profile_part("naming.preset"))
    sh.take(theme, size, 31, "profiles-search", "Profiles, searching the settings",
            lambda: profile_part("acoustid", "fingerprint"))
    win.search_box.setText("")
    import requirements as rq
    setup = win.pages["Setup"]

    def setup_page(welcome):
        win.go("Setup")
        setup.welcome(welcome)
        setup.scroll.verticalScrollBar().setValue(0)
        return win.grab
    sh.take(theme, size, 17, "setup", "Setup", lambda: setup_page(False))
    sh.take(theme, size, 18, "setup-welcome", "Setup, first start", lambda: setup_page(True))
    setup.welcome(False)

    def missing_key():
        """Submit AcoustIDs… with no AcoustID keys: what is missing, and Open Setup."""
        win.go("Overview")
        found = [t for _i, t in gui.setup.missing(("acoustid", "acoustid_user"))]
        s = sheets.Sheet(win, "AcoustID is not set up for sending fingerprints.", "\n\n".join(found),
                         "circle-alert", "attention", ("Cancel", "Open Setup"), "Open Setup")
        s.adjustSize()
        s.show()
        sh.settle(6)
        alone = s.grab()
        base = win.grab()
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 90))
        x = (base.width() // max(1, int(base.devicePixelRatio())) - alone.width() // max(1, int(alone.devicePixelRatio()))) // 2
        p.drawPixmap(QPoint(x, win.toolbar.geometry().bottom() + 8), alone)
        p.end()
        s.close()
        s.deleteLater()
        return base
    sh.take(theme, size, 19, "sheet-missing-key", "Missing key sheet", missing_key)

    def report_sheet():
        """The run report of the made-up plan, as if applied (gui.report)."""
        import report as report_mod
        from gui import report as report_gui
        win.go("Jobs")
        rep = report_mod.build(sample_report_plan(plan), sample_results(plan), job={"status": "done"})
        rep["started"] = job_time(plan["job"])
        dlg = report_gui.ReportSheet(win, rep)
        dlg.resize(min(1240 if TIDY["on"] else 1040, size[0] - 40), min(680 if TIDY["on"] else 620, size[1] - 80))
        dlg.show()
        dlg.tree.setCurrentItem(dlg.tree.topLevelItem(0).child(1))      # (the release without track lengths)
        sh.settle(10)
        alone = dlg.grab()
        base = win.grab()
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 90))
        x = (base.width() // max(1, int(base.devicePixelRatio())) - alone.width() // max(1, int(alone.devicePixelRatio()))) // 2
        p.drawPixmap(QPoint(x, win.toolbar.geometry().bottom() + 8), alone)
        p.end()
        dlg.close()
        dlg.deleteLater()
        return base
    sh.take(theme, size, 20, "report", "Report sheet", report_sheet)
    prefs = win.preferences()
    if TIDY["on"]:
        prefs.resize(760, 600)
    for nn, name in ((11, "General"), (12, "Keys"), (13, "Advanced"), (28, "Network"), (29, "Files")):
        def pane(n=name):
            prefs.show_tab(n)
            if n == "Advanced":
                import doctor
                prefs.checks.show_found([doctor.Check("python", "ok", "3.12"), doctor.Check("ffmpeg", "ok", "6.1"),
                                         doctor.Check("AcoustID application key", "warn", "not set",
                                                      rq.notice("acoustid", "app", rq.NOT_SET)),
                                         doctor.Check("Discogs Enrichment", "ok", "found")])
            if n == "Network" and TIDY["on"]:
                import connection
                from gui.setup import ConnectionPanel
                prefs.findChild(ConnectionPanel).answered([
                    connection.Result("musicbrainz", "ok", 412), connection.Result("discogs", "ok", 538),
                    connection.Result("acoustid", "ok", 297), connection.Result("coverartarchive", "ok", 365)])
            return prefs.grab
        sh.take(theme, size, nn, f"preferences-{name.lower()}", f"Preferences {name}", pane)
    prefs.close()
    win.go("Overview")
    win.raise_()
    sh.settle(4)

    def sheet():
        head, changes, undo, action = ov.apply_words()       # (the words Overview.apply asks with)
        s = sheets.Sheet(win, head, sheets.confirm_body(changes, undo), "circle-alert", "attention", ("Cancel", action),
                         action)
        s.adjustSize()
        s.show()
        sh.settle(6)
        alone = s.grab()
        sh.save(alone, theme, size, 14, "sheet-apply-alone", "Sheet")
        base = win.grab()
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 90))
        x = (base.width() // max(1, int(base.devicePixelRatio())) - alone.width() // max(1, int(alone.devicePixelRatio()))) // 2
        p.drawPixmap(QPoint(x, win.toolbar.geometry().bottom() + 8), alone)
        p.end()
        s.close()
        s.deleteLater()
        return base
    sh.take(theme, size, 14, "sheet-apply", "Sheet", sheet)

    def menu(title, sub=None):
        m = win.menus[title]
        bar = win.menuBar()
        act = m.menuAction()
        pos = bar.actionGeometry(act).bottomLeft()
        win.update_album_actions()
        m.popup(bar.mapToGlobal(pos))
        sh.settle(6)
        mp = m.grab()
        base = win.grab()
        p = QPainter(base)
        p.drawPixmap(bar.mapTo(win, pos), mp)
        if sub:                                            # (a submenu drawn open beside its item)
            s = win.menus[sub]
            r = m.actionGeometry(s.menuAction())
            s.popup(m.mapToGlobal(r.topRight()))
            sh.settle(6)
            p.drawPixmap(bar.mapTo(win, pos) + r.topRight(), s.grab())
            s.hide()
        p.end()
        m.hide()
        return base
    sh.take(theme, size, 15, "menu-file", "File menu", lambda: menu("File"))
    sh.take(theme, size, 16, "menu-album", "Album menu", lambda: menu("Album", "Change Release"))
    end = time.time() + 10
    while time.time() < end and win.threads():
        sh.settle(2)
    win.close()
    win.deleteLater()
    sh.settle(4)


def shoot_m7(sh, gui, plan, wav, theme, size, QPoint, QColor, QPainter, Qt):
    """The M7 sheets, each over the window as it opens there (dimmed behind, as a sheet)."""
    import copy
    from gui import dialogs, search
    win = gui.Main()
    win.show()
    win.resize(*size)                                   # (after show: the window first fits itself, 1180x760)
    win.inspector_wanted = True
    win.place_inspector()
    sh.settle(20)
    ov = win.pages["Overview"]
    win.go("Overview")
    ov.path = str(Path(plan["albums"][0]["source_dir"]).parent.parent)
    ov.where.setText(ov.path)
    ov.views.setCurrentWidget(ov.results)
    ov.profile.setCurrentText("Library")
    ov.planned(copy.deepcopy(plan))

    def pick(tier):
        for r in range(ov.proxy.rowCount()):
            a = ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)
            if a["status"] == "ready" and a["identification"].get("tier") == tier:
                ov.table.selectRow(r)
                sh.settle(6)
                return ov.selected()

    def idle(secs=8):
        end = time.time() + secs
        while time.time() < end and win.threads():
            sh.settle(2)
        sh.settle(6)

    def over(dlg):
        """dlg over the dimmed window, at the top centre as a sheet."""
        dlg.show()
        idle()
        alone = dlg.grab()
        base = win.grab()
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 90))
        w = alone.width() // max(1, int(alone.devicePixelRatio()))
        p.drawPixmap(QPoint(max(0, (base.width() // max(1, int(base.devicePixelRatio())) - w) // 2), win.toolbar.geometry().bottom() + 8), alone)
        p.end()
        dlg.close()
        return base

    def fit(dlg):
        dlg.resize(min(dlg.width(), size[0] - 48), min(dlg.height(), size[1] - 96))
        return dlg

    pick("album_only")
    sh.take(theme, size, 17, "search", "Search for Release", lambda: over(fit(search.open_search(ov))))
    sh.take(theme, size, 18, "other-versions", "Other Versions", lambda: over(fit(search.open_versions(ov))))
    sh.take(theme, size, 19, "choose-pressing", "Choose Your Pressing", lambda: over(fit(search.open_pressing(ov))))

    def edited():
        a = pick("exact")
        win.inspector.show_tab("Tags")
        win.inspector.set_tag("label", ["Polar Records Ltd"], album=a)
        idle()
        return win.grab
    sh.take(theme, size, 20, "inspector-tags-edited", "Inspector Tags (your edit)", edited)

    def scripts():
        pick("exact")
        dlg = win.edit_scripts()
        return over(fit(dlg))
    sh.take(theme, size, 21, "script-editor", "Script Editor", scripts)

    def tags_from_names():
        a = pick("exact")
        dlg = dialogs.TagsFromFileNames(win, dialogs.plan_files(a), None)
        return over(fit(dlg))
    sh.take(theme, size, 22, "tags-from-filenames", "Tags from File Names", tags_from_names)
    sh.take(theme, size, 23, "file-info", "File Info", lambda: over(fit(dialogs.FileInfo(win, str(wav)))))

    def log():
        v = dialogs.LogViewer(win)
        v.resize(min(960, size[0] - 80), min(600, size[1] - 80))
        v.show()
        idle()
        px = v.grab()
        v.close()
        return px
    sh.take(theme, size, 24, "log", "Log", log)
    sh.take(theme, size, 25, "job-details", "Job Details",
            lambda: over(fit(dialogs.JobDetails(win, "20261031-101500-ab12"))))

    def menu(m, pos):
        win.update_album_actions()
        m.popup(pos)
        sh.settle(6)
        mp = m.grab()
        base = win.grab()
        p = QPainter(base)
        p.drawPixmap(win.mapFromGlobal(pos), mp)
        p.end()
        m.hide()
        return base

    def tools():
        bar = win.menuBar()
        m = win.menus["Tools"]
        return menu(m, bar.mapToGlobal(bar.actionGeometry(m.menuAction()).bottomLeft()))
    win.go("Overview")
    pick("exact")
    sh.take(theme, size, 26, "menu-tools", "Tools menu", tools)

    def context():
        from PySide6.QtWidgets import QMenu
        from gui import actions
        pick("album_only")
        got = []
        with mock.patch("gui.overview.popup_menu", lambda _p, _v, _pos, acts: got.extend(acts)):
            ov.context_menu(ov.table.visualRect(ov.table.currentIndex()).center())
        m = QMenu(win)                                     # (the same items popup_menu shows, not exec'd)
        for a in got:
            m.addSeparator() if a is None else m.addAction(a)
        actions.mac_menu(m)
        # (opened near the top of the table, and never past the window's bottom: the capture grabs the
        # window only, where a real screen would move the menu up)
        rect = ov.table.visualRect(ov.table.currentIndex())
        at = ov.table.viewport().mapTo(win, QPoint(rect.left() + 48, min(rect.center().y(), rect.height())))
        at.setY(max(0, min(at.y(), win.height() - m.sizeHint().height() - 4)))
        return menu(m, win.mapToGlobal(at))
    sh.take(theme, size, 27, "menu-album-context", "Album table menu", context)
    idle(10)
    for w in (getattr(ov, "_search_sheet", None), getattr(ov, "_versions_sheet", None)):
        if w is not None:
            w.close()
    win.close()
    win.deleteLater()
    sh.settle(4)


def m8_tracks(n=11):
    """A made-up release's tracks for Match Files Manually (as manual_match.release_tracks gives them)."""
    return {"ref": "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c", "title": "Northern Lights", "artist": "Aurora Vale",
            "url": "https://musicbrainz.org/release/4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c",
            "tracks": [{"disc": 1, "number": i + 1, "title": t, "artist": "Aurora Vale", "secs": 180.0 + 17 * i,
                        "tags": {"title": [t], "tracknumber": [str(i + 1)]}}
                       for i, t in enumerate(("Polar Night", "First Light", "Magnetic North", "Aurora", "Snowline",
                                              "Ice Harbour", "Long Day", "Drift", "Signal Fires", "Thaw",
                                              "Northern Lights")[:n])]}


def m8_files(album):
    """The album's files as manual_match.file_rows gives them (the sample files hold no audio)."""
    out = []
    for i, fe in enumerate(album.get("files") or []):
        n = (i + 1) if i not in (3, 4) else (5 if i == 3 else 4)      # (two files the wrong way round)
        out.append({"path": fe["src"], "name": Path(fe["src"]).name, "title": f"Track {i + 1}", "number": n,
                    "disc": 1, "secs": 180.0 + 17 * (n - 1) + (2 if i == 6 else 0), "planned_number": i + 1,
                    "planned_disc": 1})
    return out


class _Login:
    """A made-up MusicBrainz login for the account sheets (no request is made)."""
    username = "aurora_listener"
    provider = "musicbrainz"

    def logged_in(self):
        return True

    def login_host(self):
        return "musicbrainz.org"


def shoot_m8(sh, gui, plan, found, wav, theme, size, QPoint, QColor, QPainter, Qt):
    """The M8 views: each over the window as it opens there (a sheet over the dimmed window)."""
    import copy
    import acoustid_submit as acs
    from gui import account, browser, manual_match
    from gui import acoustid_submit as gacs
    win = gui.Main()
    win.show()
    win.resize(*size)                                   # (after show: the window first fits itself, 1180x760)
    win.inspector_wanted = True
    win.place_inspector()
    sh.settle(20)
    ov = win.pages["Overview"]
    win.go("Overview")
    ov.path = str(Path(plan["albums"][0]["source_dir"]).parent.parent)
    ov.where.setText(ov.path)
    ov.views.setCurrentWidget(ov.results)
    ov.profile.setCurrentText("Library")
    ov.planned(copy.deepcopy(plan))

    def pick(tier="exact"):
        for r in range(ov.proxy.rowCount()):
            a = ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)
            if a["status"] == "ready" and a["identification"].get("tier") == tier:
                ov.table.selectRow(r)
                sh.settle(6)
                return ov.selected()

    def idle(secs=8):
        end = time.time() + secs
        while time.time() < end and win.threads():
            sh.settle(2)
        sh.settle(6)

    def over(dlg, keep=False):
        dlg.show()
        idle()
        alone = dlg.grab()
        base = win.grab()
        p = QPainter(base)
        p.fillRect(base.rect(), QColor(0, 0, 0, 90))
        w = alone.width() // max(1, int(alone.devicePixelRatio()))
        p.drawPixmap(QPoint(max(0, (base.width() // max(1, int(base.devicePixelRatio())) - w) // 2), win.toolbar.geometry().bottom() + 8), alone)
        p.end()
        if not keep:
            dlg.close()
        return base

    def fit(dlg):
        dlg.resize(min(max(dlg.width(), 560), size[0] - 48), min(dlg.height(), size[1] - 96))
        return dlg

    nn = 40
    prefs = win.preferences()

    def account_pane():
        prefs.show_tab("Account")
        sh.settle(6)
        return prefs.grab
    sh.take(theme, size, nn, "m8-preferences-account", "Preferences Account", account_pane)

    def account_lower():
        from PySide6.QtWidgets import QScrollArea
        prefs.show_tab("Account")
        scroll = prefs.account.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        sh.settle(6)
        return prefs.grab
    sh.take(theme, size, nn, "m8-preferences-account-lower", "Preferences Account (scrolled)", account_lower)
    prefs.close()
    win.raise_()

    def manual():
        a = pick()
        with mock.patch.object(manual_match, "release_tracks", lambda _lk, _ref, _p=None: m8_tracks(len(a["files"]))), \
                mock.patch.object(manual_match, "file_rows", m8_files):
            dlg = manual_match.open_manual_match(ov, a)
            idle()
        return over(fit(dlg))
    sh.take(theme, size, nn + 1, "m8-manual-match", "Match Files Manually", manual)

    def cd():
        pick("album_only")
        dlg = win.lookup_cd()
        dlg.paste.setText("1 8 166530 150 21150 41580 62905 83175 103830 123950 145060")
        dlg.use_text()
        d = dlg.disc
        if d is not None:
            dlg.found({"disc": d, "notes": [], "attached": True,
                       "rows": [dict(found[0], fit="This disc ID"), dict(found[1], fit="Same track layout"),
                                dict(found[3], fit="CUETools database link")]})
        return over(fit(dlg))
    sh.take(theme, size, nn + 2, "m8-cd-lookup", "Look Up CD", cd)

    def asked():
        pick("album_only")
        dlg = browser.open_release(ov, "release", "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c")
        return over(fit(dlg))
    sh.take(theme, size, nn + 3, "m8-browser-release", "A release the browser asked for", asked)

    def collections():
        pick()
        cols = [{"id": "c1", "name": "My CDs", "count": 412}, {"id": "c2", "name": "Wishlist", "count": 37},
                {"id": "c3", "name": "Lent to friends", "count": 4}]
        return over(account.CollectionsSheet(win, "Northern Lights", "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c",
                                             cols, {"c1"}))
    sh.take(theme, size, nn + 4, "m8-collections", "Your collections", collections)

    def ratings():
        a = pick()
        rows = [{"file": fe["src"], "title": f"Track {i + 1}", "recording": f"0000000{i}-1111-4222-8333-444455556666",
                 "stars": (5, 4, 3)[i % 3]} for i, fe in enumerate(a["files"][:4])]
        body, sent = account.ratings_text(_Login(), rows)
        return over(fit(account._send_sheet(win, f"Submit {len(rows)} ratings to MusicBrainz?", body, sent,
                                            f"Submit {len(rows)} Ratings")))
    sh.take(theme, size, nn + 5, "m8-ratings", "Submit Ratings", ratings)

    def acoustid():
        a = pick()
        s = gacs.Submission(win, a)
        s.tracks = [acs.Track(path=fe["src"], title=f"Track {i + 1}", tracknumber=str(i + 1),
                              recording_id=f"0000000{i}-1111-4222-8333-444455556666" if i != 2 else "",
                              id_from="tags" if i % 2 else "preview", fileformat="FLAC", bitrate=912,
                              fingerprint="AQAA" if i != 2 else "", duration=180 + 17 * i,
                              problem="" if i != 2 else "no MusicBrainz recording ID")
                    for i, fe in enumerate(a["files"][:6])]
        return over(fit(s.confirm_sheet()))
    sh.take(theme, size, nn + 6, "m8-acoustid", "Submit AcoustIDs", acoustid)

    def rating_row():
        pick()
        win.inspector.show_tab("Tags")
        idle()
        return win.grab
    sh.take(theme, size, nn + 7, "m8-inspector-rating", "Inspector rating row", rating_row)

    def player():
        a = pick()
        win.player.play_files([fe["src"] for fe in a["files"]], 0, "Aurora Vale — Northern Lights")
        sh.settle(10)
        return win.grab
    sh.take(theme, size, nn + 8, "m8-player", "Player bar", player)

    def menu(title):
        m = win.menus[title]
        bar = win.menuBar()
        pos = bar.actionGeometry(m.menuAction()).bottomLeft()
        win.update_album_actions()
        m.popup(bar.mapToGlobal(pos))
        sh.settle(6)
        mp = m.grab()
        base = win.grab()
        p = QPainter(base)
        p.drawPixmap(bar.mapTo(win, pos), mp)
        p.end()
        m.hide()
        return base
    pick()
    sh.take(theme, size, nn + 9, "m8-menu-controls", "Controls menu", lambda: menu("Controls"))
    sh.take(theme, size, nn + 10, "m8-menu-album", "Album menu (M8)", lambda: menu("Album"))

    # the states between: a lookup running or failed, nothing to show, a send that failed
    def until(cond, secs=8):
        end = time.time() + secs
        while time.time() < end and not cond():
            sh.settle(2)
        sh.settle(4)

    class _NoLookup:                                       # (versions.make_lookup's stand-in: never used)
        def close(self):
            pass

    def cd_state(fail):
        import threading
        import cdlookup
        pick("album_only")
        hold = threading.Event()

        def lookup(*_a, **_k):
            if fail:
                raise RuntimeError("MusicBrainz could not be reached (the network is off).")
            hold.wait(10)                                  # (held while the shot is taken)
            return {"disc": None, "rows": [], "notes": []}
        with mock.patch("gui.versions.make_lookup", lambda *_a, **_k: _NoLookup()), \
                mock.patch.object(cdlookup, "lookup", lookup):
            dlg = win.lookup_cd()
            dlg.paste.setText("1 8 166530 150 21150 41580 62905 83175 103830 123950 145060")
            dlg.use_text()
            before = dlg.status.text()
            dlg.look_up()
            if fail:
                until(lambda: dlg.status.text() != before and "Looking up" not in dlg.status.text())
            else:
                sh.settle(6)
            try:
                return over(fit(dlg), keep=not fail)
            finally:
                hold.set()
                until(lambda: not dlg.worker or not getattr(dlg.worker, "isRunning", lambda: False)())
                if not fail:
                    dlg.close()
    sh.take(theme, size, nn + 11, "m8-cd-lookup-busy", "Look Up CD (looking up)", lambda: cd_state(False))
    sh.take(theme, size, nn + 12, "m8-cd-lookup-error", "Look Up CD (failed)", lambda: cd_state(True))

    def collections_empty():
        pick()
        return over(account.CollectionsSheet(win, "Northern Lights", "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c", [],
                                             set()))
    sh.take(theme, size, nn + 13, "m8-collections-empty", "Your collections (none yet)", collections_empty)

    def acoustid_error():
        from gui import sheets
        pick()
        err = ("AcoustID refused the submission: invalid user API key. Check the AcoustID user key in "
               "Preferences > Keys.")
        return over(sheets.Sheet(win, "The fingerprints were not sent.", err, "triangle-alert", "attention",
                                 ("Close",), "Close"))
    sh.take(theme, size, nn + 14, "m8-acoustid-error", "Submit AcoustIDs (failed)", acoustid_error)

    def manual_empty():
        a = pick("album_only")
        with mock.patch.object(manual_match, "planned_ref", lambda _a: ""), \
                mock.patch.object(manual_match, "file_rows", m8_files):
            dlg = manual_match.open_manual_match(ov, a)
            idle()
            until(lambda: not dlg.loading)
        return over(fit(dlg))
    sh.take(theme, size, nn + 15, "m8-manual-match-empty", "Match Files Manually (no release)", manual_empty)
    win.player.close_player()
    idle(10)
    for w in (getattr(ov, "_search_sheet", None), getattr(ov, "_cd_sheet", None), getattr(ov, "_match_sheet", None)):
        if w is not None:
            try:
                w.close()
            except RuntimeError:
                pass
    win.close()
    win.deleteLater()
    sh.settle(4)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("round", help="the folder name for this round, e.g. r1")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "tagmender_m7_screens"))
    ap.add_argument("--only", default="", help="comma-separated parts of shot names, e.g. overview,sheet")
    ap.add_argument("--size", default="", help="one window size instead of both, e.g. 1440x900")
    ap.add_argument("--scale", type=float, default=1, help="pixels per point, e.g. 2 for high-density screens")
    ap.add_argument("--readme", action="store_true",
                    help="the README's pictures only, tidied, copied into docs/screenshots "
                         "(default size 1440x900 at scale 2)")
    args = ap.parse_args(argv)
    global SIZES
    if args.readme:
        TIDY["on"] = True
        args.size = args.size or "1440x900"
        args.scale = args.scale if args.scale != 1 else 2
        args.only = ",".join(sorted({n for _t, n in README_SHOTS.values()}))
    if args.size:
        w, h = (int(x) for x in args.size.lower().split("x"))
        SIZES = ((w, h),)
    out = Path(os.path.abspath(os.path.expanduser(args.out))) / args.round
    if str(out.resolve()).startswith("/media") or str(out).startswith("/media"):
        print("refusing to write under /media", file=sys.stderr)
        return 2
    tmp = isolate(args.scale)
    sys.path.insert(0, str(REPO))
    out.mkdir(parents=True, exist_ok=True)
    try:
        shooter = run(out, [o for o in args.only.split(",") if o])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in shooter.failed:
        print("FAILED", f, file=sys.stderr)
    if args.readme and not shooter.failed:
        size = f"{SIZES[0][0]}x{SIZES[0][1]}"
        made = {(r[2], r[0].split("-", 3)[3][:-4]): r[0] for r in shooter.manifest if r[3] == size}
        for dest, (theme, name) in README_SHOTS.items():
            src = made.get((theme, name))
            if src is None:
                print("FAILED", dest, "was not taken", file=sys.stderr)
                return 1
            shutil.copy(out / src, REPO / "docs" / "screenshots" / dest)
        print(f"{len(README_SHOTS)} pictures copied into docs/screenshots")
    print(f"{len(shooter.manifest)} shots in {out}" + (f", {len(shooter.failed)} failed" if shooter.failed else ""))
    return 1 if shooter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
