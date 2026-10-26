# Shared setup for the review-19 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the snapshot) on sys.path, helpers to make files, run a plan and undo it, and
# to read MP4 atoms.
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm19A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm19A-state-")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _review13b_common import make  # noqa: E402,F401
import executor  # noqa: E402
import payload  # noqa: E402,F401
import plan as pl  # noqa: E402
import tag_albums as ta  # noqa: E402,F401
import tagio  # noqa: E402,F401
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)


def encode(path, freq, *args, duration=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
                    *args, "-map_metadata", "-1", "-y", str(path)], check=True)
    return path


def decoded(path):
    """sha256 of the file's decoded audio (what a player plays), or the ffmpeg error."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a", "-f", "hash",
                        "-hash", "sha256", "-"], capture_output=True, text=True)
    return r.stdout.strip() + ("" if not r.stderr.strip() else " ERR " + r.stderr.strip()[:200])


def atoms(data, start=0, end=None, depth=0, out=None):
    """[(kind, offset, size, depth)] of an MP4 file's atoms (containers walked)."""
    end = len(data) if end is None else end
    out = [] if out is None else out
    p = start
    while p + 8 <= end:
        n, k = struct.unpack(">I4s", data[p:p + 8])
        h = 8
        if n == 1:
            n, h = struct.unpack(">Q", data[p + 8:p + 16])[0], 16
        elif n == 0:
            n = end - p
        if n < h:
            break
        out.append((k, p, n, depth))
        if k in (b"moov", b"moof", b"traf", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"udta"):
            atoms(data, p + h, p + n, depth + 1, out)
        p += n
    return out


def run_and_undo(t, fs, force=False, between=None, targets=None, **prof):
    """Tag (or move, per prof) fs with a plan, call between() after the job, undo it."""
    prop = t.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                      targets=targets or [target(n + 1, f"Title {n + 1}") for n in range(len(fs))])
    res = executor.Executor(pl.build([prop], t.profile(**prof))).run()
    t.assertEqual(res["counts"], {"done": 1}, res)
    if between:
        between(res["job"])
    return res, executor.undo(res["job"], force=force)


def embed_cuesheet(f):
    """A cue sheet embedded in a FLAC file (metaflac --import-cuesheet-from, CUETools)."""
    from mutagen.flac import FLAC, CueSheet, CueSheetTrack, CueSheetTrackIndex
    fl = FLAC(f)
    cs = CueSheet(None)
    cs.media_catalog_number = b"0724384260521"
    t1 = CueSheetTrack(1, 0, b"GBAYE0000001")
    t1.indexes = [CueSheetTrackIndex(1, 0)]
    cs.tracks = [t1, CueSheetTrack(170, fl.info.total_samples)]
    fl.metadata_blocks.append(cs)
    fl.save()
