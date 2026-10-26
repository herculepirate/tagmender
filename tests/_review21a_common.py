# Shared setup for the review-21 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the review-21 src snapshot) and its tests folder on sys.path, no Picard.ini of
# this machine; helpers to make files (ffmpeg), hear them, read MP4 atoms, and to run a piece
# of code with the PREVIOUS version (TM_PREV) - for jobs recorded by an earlier TagMender.
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm21A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm21A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm21A-ini-"), "no-Picard.ini")
_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
PREV = Path(os.environ.get("TM_PREV", str(_HERE.parent / "prev"))).resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _review13b_common import make  # noqa: E402,F401
import executor  # noqa: E402,F401
import payload  # noqa: E402,F401
import plan as pl  # noqa: E402,F401
import tag_albums as ta  # noqa: E402,F401
import tagio  # noqa: E402,F401
from test_executor import Base, target  # noqa: E402,F401

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


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


def strip_udta(f):
    """The M4A (moov last) without any udta atom: an M4A with no tags and no udta at all."""
    f = Path(f)
    data = bytearray(f.read_bytes())
    moov = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"moov" and d == 0)
    assert moov[0] + moov[1] == len(data), "moov is not last"
    udta = next(((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"udta" and d == 1), None)
    if udta:
        del data[udta[0]:udta[0] + udta[1]]
        data[moov[0]:moov[0] + 4] = struct.pack(">I", moov[1] - udta[1])
    f.write_bytes(bytes(data))
    return f


def with_prev(code: str, *args) -> str:
    """Run code with the previous version on sys.path (as that version would), in this test's
    XDG dirs; sys.argv[1:] are args. Its stdout back."""
    env = dict(os.environ)
    script = ("import sys, os\n"
              f"sys.path.insert(0, {str(PREV)!r}); sys.path.insert(0, {str(PREV / 'tests')!r})\n" + code)
    r = subprocess.run([sys.executable, "-c", script, *map(str, args)], capture_output=True, text=True,
                       env=env, cwd=tempfile.gettempdir())
    if r.returncode:
        raise RuntimeError(f"previous version failed: {r.stderr[-2000:]}")
    return r.stdout
