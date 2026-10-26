# Shared setup for the review-22 A tests: temp XDG dirs before any TagMender import, the repo
# (the repo) and its tests folder on sys.path, no Picard.ini of
# this machine; helpers to make a small realistic library with ffmpeg and to compare trees.
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm22A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm22A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm22A-ini-"), "no-Picard.ini")
_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(_HERE))

import executor  # noqa: E402,F401
import payload  # noqa: E402,F401
import plan as pl  # noqa: E402,F401
import profiles as pf  # noqa: E402,F401
import tag_albums as ta  # noqa: E402,F401
import tagio  # noqa: E402,F401
from test_executor import Base, target  # noqa: E402,F401

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)

CODEC = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame"], ".m4a": ["-c:a", "aac"],
         ".ogg": ["-c:a", "libvorbis"], ".opus": ["-c:a", "libopus"], ".wav": ["-c:a", "pcm_s16le"]}


def encode(path, freq, *extra, meta=None, duration=1):
    """A sine file made by ffmpeg, no metadata unless meta."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = sum((["-metadata", f"{k}={v}"] for k, v in (meta or {}).items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
                    *CODEC[path.suffix], *extra, "-map_metadata", "-1", *m, "-y", str(path)], check=True)
    return path


def jpeg(path, color=(10, 20, 30), size=(600, 600)):
    from PIL import Image
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def decoded(path):
    """sha256 of the file's decoded audio (what a player plays), or the ffmpeg error."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a", "-f", "hash",
                        "-hash", "sha256", "-"], capture_output=True, text=True)
    return r.stdout.strip() + ("" if not r.stderr.strip() else " ERR " + r.stderr.strip()[:200])


def tree(root, skip=("state", "config")):
    """{relative path: sha256 or "dir"} of everything under root but TagMender's own folders."""
    out = {}
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if os.path.relpath(os.path.join(dp, d), root).split(os.sep)[0] not in skip]
        for n in dns:
            out[os.path.relpath(os.path.join(dp, n), root)] = "dir"
        for n in fns:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, root)] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return out


def changed(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
