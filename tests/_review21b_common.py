# Shared setup for the review-21 B tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the review-21 src snapshot) and its tests folder (the repo's own helpers) on
# sys.path, no Picard.ini of this machine; helpers to make files (ffmpeg), to plan + run one
# album with tagger scripts, and to run code with the PREVIOUS version (TM_PREV). No network,
# no Picard.
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm21B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm21B-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm21B-ini-"), "no-Picard.ini")
_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
PREV = Path(os.environ.get("TM_PREV", str(_HERE.parent / "prev"))).resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(1, str(REPO / "tests"))

import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
          ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"],
          ".wma": ["-c:a", "wmav2"], ".wav": [], ".aiff": []}
_BLANK = {}

TARGET = {"title": ["T"], "album": ["Al"], "artist": ["Ar"], "albumartist": ["Ar"], "tracknumber": ["1"],
          "totaltracks": ["1"], "discogs_release_id": ["777"]}


def make(path):
    """A one-second sine file with no tags."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix
    if ext not in _BLANK:
        b = Path(tempfile.mkdtemp(prefix="tm21B-blank-")) / ("blank" + ext)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", *CODECS[ext],
                        "-map_metadata", "-1", "-y", str(b)], check=True)
        _BLANK[ext] = b
    shutil.copy(_BLANK[ext], path)
    return path


def fresh_state():
    tmp = Path(tempfile.mkdtemp(prefix="tm21B-"))
    ex.CONFIG_DIR, ex.STATE_DIR = tmp / "config", tmp / "state"
    ex._write(ex.rules_file(), {"rules": []})
    logsetup.reset()
    return tmp


def profile(lib, scripts=(), **kw):
    picard = {"enable_tagger_scripts": bool(scripts), "list_of_scripts": list(scripts), "write_id3v1": False}
    d = {"name": "t", "sources": [{"path": str(lib)}], "thresholds": {"organize_min_confidence": 95},
         "picard": picard}
    d.update(kw)
    return pf.from_dict(d)


def proposal(files, extra=None):
    tg = []
    for i, _f in enumerate(files):
        t = dict(TARGET, tracknumber=[str(i + 1)], totaltracks=[str(len(files))])
        t.update(extra or {})
        tg.append(t)
    return pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=tg,
                       provider="discogs", release_id="777", confidence=97.0, margin=20.0, evidence=["x"],
                       release={"album": ["Al"], "albumartist": ["Ar"]})


def run(files, scripts=()):
    """Plan + run one album (the files, in place) with these tagger scripts; then plan again.
    (plan, result, second plan)."""
    fresh_state()
    lib = Path(files[0]).parent.parent
    prof = profile(lib, scripts)
    prop = proposal(files)
    p = pl.build([prop], prof)
    res = executor.Executor(p).run()
    p2 = pl.build([prop], prof)
    return p, res, p2


def with_prev(code: str, *args) -> str:
    """Run code with the previous version first on sys.path, in this test's XDG dirs."""
    env = dict(os.environ)
    script = ("import sys, os\n"
              f"sys.path.insert(0, {str(PREV)!r}); sys.path.insert(1, {str(PREV / 'tests')!r})\n" + code)
    r = subprocess.run([sys.executable, "-c", script, *map(str, args)], capture_output=True, text=True,
                       env=env, cwd=tempfile.gettempdir())
    if r.returncode:
        raise RuntimeError(f"previous version failed: {r.stderr[-2000:]}")
    return r.stdout
