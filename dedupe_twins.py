#!/usr/bin/env python3
"""dedupe_twins.py - find album folders that hold every track twice (e.g.
"001.ACAaM.flac" next to "01.ACAAM.flac", left behind when an album was saved
again under a newer naming script) and move the OLDER copy of each track out
of the library.

Nothing is deleted. Older copies are MOVED to <music root>/<duplicates folder>/... (the
folder name set on this computer, Preferences > File Safety; "_Duplicates"), keeping
the library/artist/album folders, or - with "The Trash" chosen there (duplicates.dispose,
or --trash) - to the Trash of the drive each file is on, as a file manager does. Every move
is logged so it can be undone.

  1. Dry run (changes nothing, writes a plan):
       python3 dedupe_twins.py --list ~/Downloads/suspected_twin_albums.csv
  2. Look at ~/Downloads/twin_plan.csv, then do it:
       python3 dedupe_twins.py --list ~/Downloads/suspected_twin_albums.csv --apply
  Folders skipped because the dates don't decide can be finished with
  --prefer-tagged: where exactly one copy of each track carries IDs, that
  copy is kept and the untagged one moved, whatever the dates.
  3. Undo, if ever needed:
       python3 dedupe_twins.py --undo ~/Downloads/twin_moves_<time>.csv

Instead of --list you can scan whole folders: --root ~/Music/ambient

How a duplicate is recognised, per folder:
  * FLAC: the decoded audio has the same checksum (certain), or
  * any format: same title (or same file name apart from the track number)
    AND the same length to within a quarter of a second.
Which copy is kept, per duplicate set:
  * lossless beats lossy - a FLAC is kept over an Opus/MP3 copy, whatever the dates;
  * between copies in the same format, the newest is kept and older ones moved.
Subfolders of an album are included, so a FLAC/ and an Opus/ folder pair up.

A folder is SKIPPED (left untouched and listed in the plan with the reason) if:
  * the dates of same-format copies do not split cleanly into an older group
    and a newer group (some "old-style" files newer than their twins), or
  * an older copy carries MusicBrainz/Discogs IDs that its newer twin lacks
    (deleting it would lose tagging), or
  * a file's twin is not in the same folder.

Other files (rip logs, cue sheets, checksums, playlists, .nfo/.txt notes) go
with the older copies only when their date falls in the older group's time
window (10 minutes either side) and NOT near the newer group. Images are
never moved. When the two groups were written minutes apart, a file in
between can't be assigned, so it stays (the plan says so).
"""
import argparse
import csv
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import match_albums as ma     # file reading + duplicate rules shared with the matcher
except SystemExit as exc:          # mutagen missing
    sys.exit(str(exc))

# Library root: moved files go to <root>/<duplicates folder>/<same relative path>. Set TAGMENDER_MUSIC_ROOT
# or pass --music-root.
DEFAULT_MUSIC_ROOT = os.path.expanduser(os.environ.get("TAGMENDER_MUSIC_ROOT", "~/Music"))
import app_settings  # noqa: E402
DUPES = app_settings.duplicates_folder()       # the folder duplicate copies go into

# Non-audio files that belong to one particular copy of an album (rip logs,
# cue sheets, checksums, playlists, notes). Images are deliberately NOT here:
# the copy that is kept uses the same artwork.
COMPANION_EXT = {".log", ".cue", ".m3u", ".m3u8", ".txt", ".nfo", ".ffp", ".md5", ".sfv",
                 ".accurip", ".info", ".toc", ".st5", ".sha1", ".sha256"}
# A companion file belongs to a date group if it was written within this many
# seconds of that group's files, and is at least this far from the other group.
COMPANION_WINDOW_S = 600
DEFAULT_PLAN = "~/Downloads/twin_plan.csv"


def when(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def keep_order(p, info, by_tags=False):
    """Sort key: the copy to KEEP comes first - preferred format (FLAC before
    Opus/MP3, see match_albums._FORMAT_PREFERENCE), then (with by_tags) the
    copy carrying MusicBrainz/Discogs IDs, then the newest."""
    ext = p.suffix.lower()
    pref = ma._FORMAT_PREFERENCE.index(ext) if ext in ma._FORMAT_PREFERENCE else 99
    return (pref, (not info[p]["has_ids"]) if by_tags else 0, -info[p]["mtime"])


def tags_decide(sets, info):
    """True when the IDs say unambiguously which copy to keep: in every set,
    among the copies in the kept format, exactly one carries IDs."""
    for s in sets:
        fmt = s[0].suffix.lower()
        same = [p for p in s if p.suffix.lower() == fmt]
        if len(same) > 1 and sum(1 for p in same if info[p]["has_ids"]) != 1:
            return False
    return True


def find_twins(folder):
    """Duplicate sets among the audio files in an album folder (subfolders
    included). Returns (sets, info): each set is a list of Paths, the copy
    to keep first."""
    files = sorted((p for p in folder.rglob("*")
                    if p.is_file() and p.suffix.lower() in ma.AUDIO_EXT
                    and not p.name.startswith(".") and DUPES not in p.parts),
                   key=lambda p: ma._natural_key(p.relative_to(folder).as_posix()))
    info = {p: ma.read_file(p) for p in files}
    for p in files:
        info[p]["mtime"] = p.stat().st_mtime

    parent = {p: p for p in files}          # union-find over "is a copy of"

    def root(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def join(a, b):
        parent[root(a)] = root(b)

    by_md5, by_key = {}, {}
    for p in files:
        if info[p]["audio_md5"]:
            by_md5.setdefault(info[p]["audio_md5"], []).append(p)
        key = ma._track_key(p, info[p])
        if key:
            by_key.setdefault(key, []).append(p)
    for group in by_md5.values():
        for other in group[1:]:
            join(group[0], other)
    for group in by_key.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if abs(info[a]["secs"] - info[b]["secs"]) <= ma._SAME_AUDIO_S:
                    # same title and length; for two FLACs the checksum decides
                    if info[a]["audio_md5"] and info[b]["audio_md5"] \
                            and info[a]["audio_md5"] != info[b]["audio_md5"]:
                        continue
                    join(a, b)
    sets = {}
    for p in files:
        sets.setdefault(root(p), []).append(p)
    dup_sets = [sorted(s, key=lambda p: keep_order(p, info))
                for s in sets.values() if len(s) > 1]
    return dup_sets, info


def plan_folder(folder, prefer_tagged=False):
    """Decide what to move in one folder. Returns (rows, status, reason).

    prefer_tagged: when the IDs say unambiguously which copy is the tagged
    one, keep that copy and ignore dates (for folders where both copies were
    written seconds apart, or the older copy is the tagged one)."""
    sets, info = find_twins(folder)
    if not sets:
        return [], "no duplicates", ""
    by_tags = prefer_tagged and tags_decide(sets, info)
    if by_tags:
        sets = [sorted(s, key=lambda p: keep_order(p, info, by_tags=True)) for s in sets]
    keep = [s[0] for s in sets]
    move = [p for s in sets for p in s[1:]]
    rel = lambda p: p.relative_to(folder).as_posix()  # noqa: E731

    rows = []
    for s in sets:
        for p in s[1:]:
            reason = ("lossless kept over " + p.suffix.lower().lstrip(".")
                      if p.suffix.lower() != s[0].suffix.lower()
                      else "untagged copy (tagged twin kept)" if by_tags else "older copy")
            rows.append({"folder": str(folder), "move": rel(p), "keep": rel(s[0]), "why": reason,
                         "move_date": when(info[p]["mtime"]), "keep_date": when(info[s[0]]["mtime"]),
                         "same_audio": "checksum" if info[p]["audio_md5"] and
                         info[p]["audio_md5"] == info[s[0]]["audio_md5"] else "title+length",
                         "move_has_ids": info[p]["has_ids"], "keep_has_ids": info[s[0]]["has_ids"]})
    # "Group by date", for copies in the SAME format: every one moved must be
    # older than every one kept, i.e. they split into an older and a newer group.
    # (A lossy copy is moved because of its format, so its date doesn't matter.)
    same_fmt_moved = [p for s in sets for p in s[1:] if p.suffix.lower() == s[0].suffix.lower()]
    same_fmt_kept = [s[0] for s in sets if any(p.suffix.lower() == s[0].suffix.lower() for p in s[1:])]
    if same_fmt_moved and not by_tags:
        newest_moved = max(info[p]["mtime"] for p in same_fmt_moved)
        oldest_kept = min(info[p]["mtime"] for p in same_fmt_kept)
        if newest_moved >= oldest_kept:
            return rows, "skipped", ("dates do not split into an older and a newer group "
                                     f"(newest older-copy {when(newest_moved)}, "
                                     f"oldest kept {when(oldest_kept)})")
    # Never lose tagging between copies of the same format. A tagged lossy copy
    # still gives way to the FLAC (your rule), but the plan says the FLAC
    # needs tagging.
    lost = [r["move"] for r in rows if r["move_has_ids"] and not r["keep_has_ids"]
            and Path(r["move"]).suffix.lower() == Path(r["keep"]).suffix.lower()]
    if lost:
        return rows, "skipped", f"older copy has IDs its twin lacks: {', '.join(lost[:3])}"
    for r in rows:
        if r["move_has_ids"] and not r["keep_has_ids"]:
            r["why"] += "; this copy had IDs - tag the kept FLAC"
    # Companion files (log, cue, ...) dated inside the older group's window.
    companions, unsure = [], []
    if same_fmt_moved:
        old_lo = min(info[p]["mtime"] for p in same_fmt_moved) - COMPANION_WINDOW_S
        old_hi = max(info[p]["mtime"] for p in same_fmt_moved) + COMPANION_WINDOW_S
        new_lo = min(info[p]["mtime"] for p in same_fmt_kept) - COMPANION_WINDOW_S
        new_hi = max(info[p]["mtime"] for p in same_fmt_kept) + COMPANION_WINDOW_S
        for c in sorted(folder.rglob("*")):
            if not c.is_file() or c.suffix.lower() not in COMPANION_EXT or DUPES in c.parts:
                continue
            t = c.stat().st_mtime
            # "old" = the group being moved, "new" = the group kept
            in_old, in_new = old_lo <= t <= old_hi, new_lo <= t <= new_hi
            if in_old and not in_new:
                companions.append(c)
                rows.append({"folder": str(folder), "move": rel(c), "keep": "",
                             "why": "other file from the older date group",
                             "move_date": when(t), "keep_date": "", "same_audio": "",
                             "move_has_ids": "", "keep_has_ids": ""})
            elif in_old and in_new:
                unsure.append(rel(c))

    n_fmt = len(move) - len(same_fmt_moved)
    parts = []
    if same_fmt_moved:
        parts.append(f"{len(same_fmt_moved)} {'untagged' if by_tags else 'older'} same-format file(s)")
    if n_fmt:
        parts.append(f"{n_fmt} lossy copy/copies of FLAC tracks")
    if companions:
        parts.append(f"{len(companions)} log/cue/other file(s) from the older group")
    note = (f"; left in place (dated between the two groups): {', '.join(unsure[:4])}"
            if unsure else "")
    return rows, "ready", f"move {' and '.join(parts)}; keep {len(keep)}{note}"


def trash_path(path, music_root):
    rel = Path(path).resolve().relative_to(Path(music_root).resolve())
    return Path(music_root) / DUPES / rel


def trash_dir(path) -> Path:
    """The Trash for PATH (the freedesktop.org Trash specification): the home Trash for a file
    on the same drive as it, else <drive top>/.Trash/<uid> when that is a shared Trash folder
    (sticky, not a link), else <drive top>/.Trash-<uid>. Never one on another drive: a move
    there would be a copy."""
    path = Path(path).absolute()
    home = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "Trash"
    dev = path.stat().st_dev
    try:
        home_dev = (home if home.exists() else home.parent).stat().st_dev
    except OSError:
        home_dev = None
    if home_dev == dev:
        return home
    top = path
    while top.parent != top and top.parent.stat().st_dev == dev:
        top = top.parent
    shared = top / ".Trash"
    try:
        st = shared.lstat()
        if not shared.is_symlink() and shared.is_dir() and st.st_mode & 0o1000:
            return shared / str(os.getuid())
    except OSError:
        pass
    return top / f".Trash-{os.getuid()}"


def to_trash(src) -> Path:
    """Move SRC into its Trash (trash_dir), with the .trashinfo file managers read to put it
    back; the name made unique there, never over another. Returns where it went."""
    from urllib.parse import quote
    src = Path(src).absolute()
    base = trash_dir(src)
    files, info = base / "files", base / "info"
    for d in (base, files, info):
        d.mkdir(mode=0o700, exist_ok=True)
    stem, n = src.name, 1
    while True:
        name = stem if n == 1 else f"{Path(stem).stem}.{n}{Path(stem).suffix}"
        try:                                  # (the .trashinfo first, exclusively: it claims the name)
            fd = os.open(info / f"{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            n += 1
            continue
        if (files / name).exists():
            os.close(fd)
            os.unlink(info / f"{name}.trashinfo")
            n += 1
            continue
        break
    # (Path= the name's own bytes, percent-encoded - a Latin-1 name too; relative to the drive's
    # top in a drive's Trash, absolute in the home one, as the specification asks)
    top = _trash_top(base)
    where = os.fsencode(src.relative_to(top) if top is not None else src)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"[Trash Info]\nPath={quote(where)}\n"
                     f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
        os.rename(src, files / name)          # (the same drive: a rename)
    except BaseException:
        Path(info / f"{name}.trashinfo").unlink(missing_ok=True)
        raise
    return files / name


def _trash_top(base: Path) -> Path | None:
    """The drive top a drive's Trash belongs to (<top>/.Trash-<uid>, <top>/.Trash/<uid>); None for
    the home Trash."""
    if base.name == f".Trash-{os.getuid()}":
        return base.parent
    if base.name == str(os.getuid()) and base.parent.name == ".Trash":
        return base.parent.parent
    return None


def _trash_info(moved: Path) -> Path | None:
    """The .trashinfo of a file moved into a Trash's files/ folder (None for any other place)."""
    if moved.parent.name != "files":
        return None
    info = moved.parent.parent / "info" / f"{moved.name}.trashinfo"
    return info if info.exists() else None


def folders_from_args(args):
    """Album folders to check. --root follows the library layout
    <root>/<Album Artist>/<Date - Album>/ (subfolders belong to the album)."""
    out = []
    if args.list:
        with open(Path(args.list).expanduser(), encoding="utf-8", errors="surrogateescape") as fh:
            for r in csv.DictReader(fh):
                out.append(Path(r["FullPath"]))
    for root in args.root or []:
        root = Path(root).expanduser()
        for artist in sorted(p for p in root.iterdir() if p.is_dir() and p.name != DUPES):
            out.extend(sorted(p for p in artist.iterdir() if p.is_dir()))
    seen, unique = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def undo(log_path):
    restored = missing = 0
    with open(Path(log_path).expanduser(), encoding="utf-8", errors="surrogateescape") as fh:
        for r in csv.DictReader(fh):
            src, dst = Path(r["moved_to"]), Path(r["original"])
            if src.exists() and not dst.exists():
                info = _trash_info(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                if info is not None:          # (out of the Trash: its entry there goes too)
                    info.unlink(missing_ok=True)
                restored += 1
            else:
                missing += 1
    print(f"Restored {restored} file(s); {missing} could not be restored "
          "(already back, or the moved copy is gone).")


def main():
    for stream in (sys.stdout, sys.stderr):           # (a Latin-1 file name printed, never a crash)
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", help="CSV with a FullPath column (e.g. suspected_twin_albums.csv)")
    ap.add_argument("--root", action="append", help="scan every folder under this path")
    ap.add_argument("--music-root", default=DEFAULT_MUSIC_ROOT,
                    help=f"moved files go to <music-root>/{DUPES}/<same relative path>")
    ap.add_argument("--plan", default=DEFAULT_PLAN, help="where to write the plan CSV")
    ap.add_argument("--apply", action="store_true", help="actually move the older copies")
    ap.add_argument("--prefer-tagged", action="store_true",
                    help="where exactly one copy of each track carries MusicBrainz/Discogs IDs, "
                         "keep that copy regardless of dates")
    ap.add_argument("--trash", action="store_true",
                    help="move the older copies to the Trash instead (default: the File Safety setting)")
    ap.add_argument("--undo", help="move files back using a twin_moves_*.csv log")
    args = ap.parse_args()
    trash = args.trash or app_settings.get("duplicates.dispose") == "trash"

    if args.undo:
        undo(args.undo)
        return
    if not args.list and not args.root:
        ap.error("give --list or --root")

    folders = folders_from_args(args)
    print(f"Checking {len(folders)} folder(s)...")
    plan_rows, summary = [], {"ready": 0, "skipped": 0, "no duplicates": 0, "missing": 0}
    ready = []
    for i, folder in enumerate(folders, 1):
        if not folder.is_dir():
            summary["missing"] += 1
            plan_rows.append({"folder": str(folder), "status": "missing", "reason": "folder not found"})
            continue
        try:
            rows, status, reason = plan_folder(folder, prefer_tagged=args.prefer_tagged)
        except Exception as exc:
            rows, status, reason = [], "skipped", f"error: {type(exc).__name__}: {exc}"
        summary[status] = summary.get(status, 0) + 1
        if status == "no duplicates":
            continue
        plan_rows.append({"folder": str(folder), "status": status, "reason": reason})
        for r in rows:
            plan_rows.append(dict(r, status=status, reason=""))
        if status == "ready":
            ready.append((folder, rows))
        if i % 25 == 0:
            print(f"  ...{i}/{len(folders)}")

    plan_path = Path(args.plan).expanduser()
    cols = ["status", "folder", "reason", "move", "why", "move_date", "keep", "keep_date",
            "same_audio", "move_has_ids", "keep_has_ids"]
    with open(plan_path, "w", newline="", encoding="utf-8", errors="surrogateescape") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(plan_rows)
    n_files = sum(len(rows) for _f, rows in ready)
    print(f"\nFolders with duplicates ready to clean: {summary['ready']} ({n_files} older files)")
    print(f"Folders skipped for a human to look at: {summary['skipped']}")
    print(f"Folders with no duplicates: {summary['no duplicates']}   not found: {summary['missing']}")
    print(f"Plan: {plan_path}")

    if not args.apply:
        print("\nDry run - nothing was changed. Add --apply to move the older copies.")
        return

    log_path = plan_path.parent / f"twin_moves_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    moved = 0
    with open(log_path, "w", newline="", encoding="utf-8", errors="surrogateescape") as fh:
        w = csv.writer(fh)
        w.writerow(["original", "moved_to", "kept_twin"])
        for folder, rows in ready:
            for r in rows:
                src = folder / r["move"]
                keep = folder / r["keep"] if r["keep"] else folder
                if not src.exists() or not keep.exists():
                    print(f"  skipped (file changed since planning): {src}")
                    continue
                if trash:
                    try:
                        dst = to_trash(src)
                    except (OSError, ValueError) as exc:
                        print(f"  skipped (could not be moved to the Trash: {exc}): {src}")
                        continue
                    w.writerow([str(src), str(dst), str(keep)])
                    fh.flush()
                    moved += 1
                    continue
                try:
                    dst = trash_path(src, args.music_root)
                except ValueError:
                    print(f"  skipped (not under {args.music_root}): {src}")
                    continue
                if dst.exists():
                    print(f"  skipped (already in {DUPES}): {dst}")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                w.writerow([str(src), str(dst), str(keep)])
                fh.flush()
                moved += 1
    print(f"\nMoved {moved} older file(s) to " + ("the Trash" if trash else str(Path(args.music_root) / DUPES)))
    print(f"Undo log: {log_path}")
    print("Re-run audit_untagged.py afterwards: these albums should no longer show as 'partial'.")


if __name__ == "__main__":
    main()
