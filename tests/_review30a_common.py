# Shared setup for the review-30 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the snapshot this folder sits beside) and its tests folder on sys.path, no
# Picard.ini of this machine, no network. Small helpers: blank audio files made by ffmpeg, a
# snapshot of a tree, and plan + run (+ undo) of one album with pl.build.
import hashlib
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm30A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm30A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm30A-ini-"), "no-Picard.ini")
HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
PREV = Path(os.environ.get("TM_PREV", str(HERE.parent / "prev"))).resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(HERE))


def _no_network(*a, **k):
    raise OSError("network off in tests")


socket.create_connection = _no_network

import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

CODEC = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], ".m4a": ["-c:a", "aac"],
         ".ogg": ["-c:a", "libvorbis"], ".opus": ["-c:a", "libopus"], ".wav": ["-c:a", "pcm_s16le"]}
_BLANK = {}


def encode(path, freq=440, secs=1):
    """A sine file made by ffmpeg, no metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (path.suffix.lower(), freq, secs)
    if key not in _BLANK:
        b = Path(tempfile.mkdtemp(prefix="tm30A-blank-")) / ("blank" + path.suffix.lower())
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                        *CODEC[path.suffix.lower()], "-map_metadata", "-1", "-y", str(b)], check=True)
        _BLANK[key] = b
    shutil.copy(_BLANK[key], path)
    return path


def jpeg_bytes(color, size=300):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (size, size), color).save(b, "JPEG", quality=90)
    return b.getvalue()


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(root, skip=()):
    """{relative path: ("file", sha256) | ("link", target) | ("dir", None)} of everything under root."""
    out = {}
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if os.path.relpath(os.path.join(dp, d), root) not in skip]
        for n in dns + fns:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir", None)
            else:
                out[rel] = ("file", hashlib.sha256(Path(p).read_bytes()).hexdigest())
    return out


def fresh_state(prefix="tm30A-"):
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    ex.CONFIG_DIR, ex.STATE_DIR = tmp / "config", tmp / "state"
    ex._write(ex.rules_file(), {"rules": []})
    logsetup.reset()
    return tmp


TARGET = {"album": ["Album"], "artist": ["Band"], "albumartist": ["Band"], "date": ["2005-01-01"],
          "discnumber": ["1"], "totaldiscs": ["1"], "musicbrainz_albumid": ["b84ee12a-09ef-421b-82de-0441a926375b"]}


def targets(n, **extra):
    out = []
    for i in range(n):
        t = {k: list(v) for k, v in TARGET.items()}
        t.update(title=[f"Track {i + 1}"], tracknumber=[str(i + 1)], totaltracks=[str(n)])
        for k, v in extra.items():
            t[k] = list(v) if isinstance(v, list) else [v]
        out.append(t)
    return out


def profile(lib, picard=None, **kw):
    st = {"write_id3v1": False}
    st.update(picard or {})
    d = {"name": "t", "sources": [{"path": str(lib)}], "artwork": {"enabled": False}, "mark_resolved": False,
         "tag_level": "enthusiast", "audiophile": {"lyrics": False, "acoustid": False, "replaygain": False},
         "picard": st}
    d.update(kw)
    return pf.from_dict(d)


def proposal(files, tgts, confidence=100.0, **kw):
    return pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=tgts,
                       provider="musicbrainz", release_id=TARGET["musicbrainz_albumid"][0], confidence=confidence,
                       margin=50.0, evidence=["x"], release={"album": ["Album"], "albumartist": ["Band"]}, **kw)


def detail(p, res=None):
    s = f"plan: {[(a.get('status'), a.get('conflicts')) for a in p['albums']]}"
    if res is not None:
        s += f"; result: {res.get('counts')} {[a.get('detail') for a in res.get('albums') or []]}"
    return s


def run_child(code, *args, repo=None, env=None, timeout=300):
    e = dict(os.environ, TM_REPO=str(repo or REPO), **(env or {}))
    return subprocess.run([sys.executable, "-c", code, str(repo or REPO), *map(str, args)], env=e,
                          capture_output=True, text=True, timeout=timeout)
