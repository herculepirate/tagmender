# Shared setup for the review-23 B tests: temp XDG dirs before any TagMender import, the repo
# (the repo) on sys.path, no Picard.ini of this machine;
# helpers to make blank audio files (ffmpeg) and to plan + run one album with tagger scripts
# and Picard settings. No network, no Picard.
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm23B-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm23B-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm23B-ini-"), "no-Picard.ini")
_SNAP = str(Path(__file__).resolve().parent.parent)
REPO = Path(_SNAP)
sys.path.insert(0, str(REPO))

import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

CODECS = {".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"], ".ogg": ["-c:a", "libvorbis"],
          ".opus": ["-c:a", "libopus"], ".m4a": ["-c:a", "aac"], ".wv": ["-c:a", "wavpack"],
          ".wma": ["-c:a", "wmav2"], ".wav": []}
_BLANK = {}

# what mb_tags.release_tags gives for a plain one-disc release (the fields that matter here)
TARGET = {"title": ["Speak to Me"], "album": ["The Dark Side of the Moon"], "artist": ["Pink Floyd"],
          "albumartist": ["Pink Floyd"], "date": ["1973-03-24"], "tracknumber": ["1"], "totaltracks": ["2"],
          "discnumber": ["1"], "totaldiscs": ["1"],
          "musicbrainz_albumid": ["b84ee12a-09ef-421b-82de-0441a926375b"]}


def make(path):
    """A one-second sine file with no tags."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix
    if ext not in _BLANK:
        b = Path(tempfile.mkdtemp(prefix="tm23B-blank-")) / ("blank" + ext)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", *CODECS[ext],
                        "-map_metadata", "-1", "-y", str(b)], check=True)
        _BLANK[ext] = b
    shutil.copy(_BLANK[ext], path)
    return path


def album(ext, n=2):
    d = Path(tempfile.mkdtemp(prefix="tm23B-lib-"))
    return [make(d / "lib" / "album" / ("track%02d%s" % (i + 1, ext))) for i in range(n)]


def fresh_state():
    tmp = Path(tempfile.mkdtemp(prefix="tm23B-"))
    ex.CONFIG_DIR, ex.STATE_DIR = tmp / "config", tmp / "state"
    ex._write(ex.rules_file(), {"rules": []})
    logsetup.reset()
    return tmp


def profile(lib, scripts=(), picard=None, **kw):
    st = {"enable_tagger_scripts": bool(scripts), "list_of_scripts": list(scripts), "write_id3v1": False}
    st.update(picard or {})
    d = {"name": "t", "sources": [{"path": str(lib)}], "thresholds": {"organize_min_confidence": 95},
         "artwork": {"enabled": False}, "mark_resolved": False, "picard": st}
    d.update(kw)
    return pf.from_dict(d)


def proposal(files, extra=None):
    tg = []
    for i, _f in enumerate(files):
        t = {k: list(v) for k, v in TARGET.items()}
        t.update(tracknumber=[str(i + 1)], totaltracks=[str(len(files))], title=["Track %d" % (i + 1)])
        t.update(extra or {})
        tg.append(t)
    return pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=tg,
                       provider="musicbrainz", release_id=TARGET["musicbrainz_albumid"][0], confidence=100.0,
                       margin=50.0, evidence=["x"],
                       release={"album": TARGET["album"], "albumartist": TARGET["albumartist"]})


def run(files, scripts=(), picard=None, extra=None, **kw):
    """Plan + run one album (in place unless kw says otherwise). (plan, result)."""
    fresh_state()
    lib = Path(files[0]).parent.parent
    prof = profile(lib, scripts, picard, **kw)
    p = pl.build([proposal(files, extra)], prof)
    res = executor.Executor(p).run()
    return p, res


def detail(res):
    return "; ".join(str(a.get("detail")) for a in res.get("albums") or [])
