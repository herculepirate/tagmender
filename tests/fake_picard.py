#!/usr/bin/env python3
"""Stand-in for Picard in tests: obeys -e LOAD/PAUSE/SAVE_MATCHED/QUIT like Picard does with
files carrying a release ID: tags them (track ID + Discogs), renames, moves them with their
scans to <root>/<New Artist>/<Year - Album>/, and removes the emptied folder.
FAKE_PICARD_ROOT: library root. FAKE_PICARD_SKIP: folder-name text to leave unsaved.
FAKE_PICARD_SKIP_FILES: file-name texts (comma-separated) of tracks it leaves unsaved and in place
(a track it could not match: the album is saved in part).
FAKE_PICARD_MOVE_ONLY=1: moves the files without saving them (no tags written).
FAKE_PICARD_WRITE: file names (comma-separated) it writes into the new folder (a cover it saved).
FAKE_PICARD_LOSE: suffix of moved files that then go missing (taken out of the library).
FAKE_PICARD_DEST: one folder name every album is filed into (discs of a set, or two releases,
in one folder; the track names then start with the album's number in the load order).
FAKE_PICARD_ONLY_SKIP_IN: FAKE_PICARD_SKIP_FILES applies only in folders whose name holds it.
FAKE_PICARD_HANGUP=1: the run that started it gets SIGHUP (its terminal was closed) before
anything is saved, and the fake ends.
FAKE_PICARD_HANGUP=after: the same, after it has saved and moved every album.
FAKE_PICARD_LOCK=1: the old album folder is left read-only (nothing can be moved back into it)."""
import os, shutil, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tag_albums.py is one level up
import mutagen
from mutagen.id3 import ID3, TXXX, TIT2
from mutagen.mp4 import MP4, MP4FreeForm
import tag_albums as ta

root = Path(os.environ["FAKE_PICARD_ROOT"])
skip = os.environ.get("FAKE_PICARD_SKIP", "")
skip_files = [x for x in os.environ.get("FAKE_PICARD_SKIP_FILES", "").split(",") if x]
move_only = os.environ.get("FAKE_PICARD_MOVE_ONLY") == "1"
skip_in = os.environ.get("FAKE_PICARD_ONLY_SKIP_IN", "")
if os.environ.get("FAKE_PICARD_HANGUP") == "1":
    import signal
    os.kill(os.getppid(), signal.SIGHUP)
    sys.exit(0)
argv = sys.argv[1:]
loads = [argv[i + 2] for i, a in enumerate(argv) if a == "-e" and argv[i + 1] == "LOAD"]
one_dest = os.environ.get("FAKE_PICARD_DEST", "")
for k, folder in enumerate(loads, 1):
    if skip and skip in folder:
        continue
    files = ta.audio_files(folder)
    mbid = ta.read_ids(files[0])["albumid"]
    dest = root / "New Artist" / (one_dest or f"1999 - {Path(folder).name} (tagged)")
    pre = f"{k}-" if one_dest else ""
    dest.mkdir(parents=True, exist_ok=True)
    for n, p in enumerate(files, 1):
        if any(x in p.name for x in skip_files) and skip_in in Path(folder).name:
            continue
        f = mutagen.File(p)
        if move_only:
            pass
        elif isinstance(f, MP4):
            f.tags.clear()
            f.tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [MP4FreeForm(mbid.encode())]
            f.tags["----:com.apple.iTunes:MusicBrainz Release Track Id"] = [MP4FreeForm(f"{mbid[:8]}-t{n}".encode())]
            f.tags["----:com.apple.iTunes:DISCOGS_RELEASE_ID"] = [MP4FreeForm(b"123")]
            f.tags["\xa9nam"] = [f"Song {n}"]
            f.save()
        elif isinstance(f.tags, ID3):
            f.tags.clear()
            for d, v in (("MusicBrainz Album Id", mbid), ("MusicBrainz Release Track Id", f"{mbid[:8]}-t{n}"), ("DISCOGS_RELEASE_ID", "123")):
                f.tags.add(TXXX(encoding=3, desc=d, text=[v]))
            f.tags.add(TIT2(encoding=3, text=[f"Song {n}"]))
            f.save()
        else:
            f.tags.clear()
            f.tags["MUSICBRAINZ_ALBUMID"] = [mbid]
            f.tags["MUSICBRAINZ_RELEASETRACKID"] = [f"{mbid[:8]}-t{n}"]
            f.tags["DISCOGS_RELEASE_ID"] = ["123"]
            f.tags["TITLE"] = [f"Song {n}"]
            f.save()
        shutil.move(str(p), str(dest / f"{pre}{n:02d}. Song {n}{p.suffix}"))
    # Like Picard's "move additional files": files next to the music that match the pattern
    # (FAKE_PICARD_LEAVE lists names that do not), and folders whose name matches it
    # (FAKE_PICARD_MOVE_DIRS); other subfolders stay behind.
    leave = {x.strip().casefold() for x in os.environ.get("FAKE_PICARD_LEAVE", "").split(",") if x.strip()}
    move_dirs = {x.strip() for x in os.environ.get("FAKE_PICARD_MOVE_DIRS", "").split(",") if x.strip()}
    for rest in sorted(Path(folder).iterdir()):
        if rest.is_file() and rest.suffix.lower() in ta.AUDIO_EXT:
            continue                                  # (a track left unsaved stays where it is)
        if rest.is_file() and rest.name.casefold() not in leave:
            shutil.move(str(rest), str(dest / rest.name))
        elif rest.is_dir() and rest.name in move_dirs:
            shutil.move(str(rest), str(dest / rest.name))
    for name in [x for x in os.environ.get("FAKE_PICARD_WRITE", "").split(",") if x]:
        (dest / name).write_bytes(b"written by the tagger")
    # Like Picard's "delete empty directories": only if nothing is left
    try:
        Path(folder).rmdir()
        Path(folder).parent.rmdir()
    except OSError:
        pass
    lose = os.environ.get("FAKE_PICARD_LOSE", "")
    if lose:
        lost = root.parent / "lost"
        lost.mkdir(exist_ok=True)
        for p in dest.glob(f"*{lose}"):
            shutil.move(str(p), str(lost / (p.name + ".gone")))
    if os.environ.get("FAKE_PICARD_LOCK") == "1" and Path(folder).is_dir():
        Path(folder).chmod(0o555)
if os.environ.get("FAKE_PICARD_HANGUP") == "after":
    import signal
    os.kill(os.getppid(), signal.SIGHUP)
