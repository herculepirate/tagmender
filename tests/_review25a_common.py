# Shared setup for the review-25 A tests: temp XDG dirs before any TagMender import, the repo
# (the repo) and its tests folder on sys.path, no Picard.ini of
# this machine, no network. A small library made with ffmpeg, driven through Engine.plan with an
# offline identifier (each file's audio digest -> its release), an offline cover source and an
# offline artist-photo source - what a user's first run does, without the providers.
import hashlib
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm25A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm25A-state-")
os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(prefix="tm25A-ini-"), "no-Picard.ini")
_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
PREV = Path(os.environ.get("TM_PREV", str(_HERE.parent / "prev"))).resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(_HERE))

import artwork  # noqa: E402
import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import logsetup  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

CODEC = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], ".m4a": ["-c:a", "aac"],
         ".ogg": ["-c:a", "libvorbis"], ".opus": ["-c:a", "libopus"], ".wav": ["-c:a", "pcm_s16le"]}


def encode(path, freq, secs=1):
    """A sine file made by ffmpeg, no metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                    *CODEC[path.suffix.lower()], "-map_metadata", "-1", "-y", str(path)], check=True)
    return path


def jpeg_bytes(color, size=(600, 600)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def jpeg(path, color=(10, 20, 30), size=(600, 600)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg_bytes(color, size))
    return path


def snapshot(root, skip=("state", "config")):
    """{relative path: (kind, sha256 or link target or None)} of everything under root but
    TagMender's own folders: a symlink is told from a file."""
    out = {}
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if os.path.relpath(os.path.join(dp, d), root).split(os.sep)[0] not in skip]
        for n in dns:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, root)] = ("link", os.readlink(p)) if os.path.islink(p) else ("dir", None)
        for n in fns:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                out[os.path.relpath(p, root)] = ("link", os.readlink(p))
            else:
                out[os.path.relpath(p, root)] = ("file", hashlib.sha256(Path(p).read_bytes()).hexdigest())
    return out


def changed(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


TARGET_EXTRA = {}          # field -> "... {n}": every target gets it (also in the earlier version's run)


def target(n, total, album, artist, **extra):
    t = {"album": [album], "albumartist": [artist], "artist": [artist], "title": [f"{album} {n}"],
         "tracknumber": [str(n)], "totaltracks": [str(total)], "discnumber": ["1"], "totaldiscs": ["1"],
         "date": ["2001-05-01"], "genre": ["Rock"], "releasetype": ["album"], "media": ["CD"],
         "musicbrainz_albumid": ["%08d-0000-0000-0000-000000000000" % (zlib.crc32(album.encode()) % 10 ** 8)],
         "musicbrainz_albumartistid": ["%08d-1111-2222-3333-444444444444" % (zlib.crc32(artist.encode()) % 10 ** 8)]}
    t.update(extra)
    for k, fmt in TARGET_EXTRA.items():
        t[k] = [fmt(n) if callable(fmt) else fmt.format(n=n, album=album, artist=artist)]
    return t


class Identifier:
    """Engine._identify, offline: each file's audio digest names its release and track."""

    def __init__(self):
        self.by, self.paths = {}, {}

    def add(self, files, album, artist):
        for k, f in enumerate(files):
            self.by[payload.digest(f)] = self.paths[str(f)] = (album, artist, k + 1, len(files))

    def __call__(self, eng, g):
        hits = [self.by.get(payload.digest(f)) or self.paths.get(str(f)) for f in g.files]
        files = [str(f) for f in g.files]
        if None in hits or len({h[:2] for h in hits}) != 1:
            return pl.Proposal(source_dir=str(g.folder), files=files, status="unresolved", reason="x",
                               reason_codes=["no_match"])
        order = sorted(range(len(files)), key=lambda i: hits[i][2])
        album, artist = hits[0][:2]
        return pl.Proposal(source_dir=str(g.folder), files=[files[i] for i in order],
                           targets=[target(hits[i][2], hits[i][3], album, artist) for i in order],
                           provider="discogs", release_id="1", confidence=99.0, margin=20.0, evidence=["x"],
                           release={"album": [album], "albumartist": [artist]})


class FakeFront:
    """A cover source with a large cover for every release."""
    name = "caa"
    min_px, enlarge, generate_mode, limit = 500, True, "off", 20_000_000

    def __init__(self, staging):
        self.staging = Path(staging)

    def front(self, folder, ids, artist="", album=""):
        path, w, h = artwork._stage(jpeg_bytes((200, 100, 50), (800, 800)), self.staging)
        return {"front": str(path), "source": "fake", "width": w, "height": h}


class FakeCoverUp:
    """CoverUp's artist sources, offline."""

    def __init__(self, staging, *a, **k):
        self.staging = Path(staging)

    def artist_photo(self, folder, ids, artist="", files=None):
        path, w, h = artwork._stage(jpeg_bytes((9, 99, 199), (1000, 1000)), self.staging)
        return {"image": str(path), "width": w, "height": h, "source": "coverup:fanart"}


def _no_network(*a, **k):
    raise OSError("network off in tests")


class Case(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm25A-"))
        self.lib, self.dest = self.root / "lib", self.root / "dest"
        self.lib.mkdir()
        self.dest.mkdir()
        ex.CONFIG_DIR, ex.STATE_DIR = self.root / "config", self.root / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()
        TARGET_EXTRA.clear()
        self.ident = Identifier()
        for p in (mock.patch.object(socket, "create_connection", _no_network),
                  mock.patch.object(artwork, "CoverUp", FakeCoverUp),
                  mock.patch.object(artwork, "chain", lambda prof, staging, *a, **k: [FakeFront(staging)]),
                  mock.patch.object(engine.Engine, "_identify", lambda eng, g: self.ident(eng, g))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.root, True)

    def album(self, folder, album, artist, n=2, ext=".flac", freq=None):
        freq = freq or (300 + zlib.crc32(str(folder).encode()) % 2000)
        files = [encode(Path(folder) / f"{k:02d} Track {k}{ext}", freq + 10 * k) for k in range(1, n + 1)]
        self.ident.add(files, album, artist)
        return files

    def profile(self, op="in_place", level="enthusiast", **extra):
        d = {"name": "t", "sources": [{"path": str(self.lib)}], "tag_level": level,
             "audiophile": {"lyrics": False, "acoustid": False, "replaygain": False}}
        if op != "in_place":
            d.update(operation=op, destination=str(self.dest))
        d.update(extra)
        return pf.from_dict(d)

    def artist_profile(self, op, file, level="enthusiast"):
        art = dict(pf.DEFAULTS["artwork"])
        art["artist"] = {"enabled": True, "file": file, "embed": True}
        return self.profile(op, level, artwork=art)

    def plan(self, prof, where=None):
        return engine.Engine(prof).plan(str(where or self.lib))

    def run_plan(self, p):
        res = executor.Executor(p).run()
        self.assertFalse(res["counts"].get("failed") or res["counts"].get("conflict"), res)
        return res


# ---- an earlier TagMender (the version before the last fix rounds) on the same library -----
_CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import _review25a_common as c
from pathlib import Path
from unittest import mock
import socket
spec = json.loads(Path(sys.argv[2]).read_text())
c.TARGET_EXTRA.update(spec.get("target_extra") or {})
root = Path(spec["root"])
c.ex.CONFIG_DIR, c.ex.STATE_DIR = root / "config", root / "state"
c.logsetup.reset()
ident = c.Identifier()
for f, hit in spec.get("ident", {}).items():
    ident.paths[f] = tuple(hit)
    try:
        ident.by[c.payload.digest(f)] = tuple(hit)
    except Exception:
        pass
for p in (mock.patch.object(socket, "create_connection", c._no_network),
          mock.patch.object(c.artwork, "CoverUp", c.FakeCoverUp),
          mock.patch.object(c.artwork, "chain", lambda prof, staging, *a, **k: [c.FakeFront(staging)]),
          mock.patch.object(c.engine.Engine, "_identify", lambda eng, g: ident(eng, g))):
    p.start()
prof = c.pf.from_dict(spec["profile"])
p = c.engine.Engine(prof).plan(spec["where"])
if spec.get("plan_only"):
    c.pl.save(p, spec["plan_only"])
    Path(spec["out"]).write_text(json.dumps({"job": p["job"], "plan": spec["plan_only"], "repo": str(c.REPO)}))
    sys.exit(0)
res = c.executor.Executor(p).run()
Path(spec["out"]).write_text(json.dumps({"job": res["job"], "counts": res["counts"],
                                         "repo": str(c.REPO)}, default=str))
'''


def run_earlier(root, profile_dict, where, ident=None, repo=None, plan_only=None):
    """Plan and run a job with the earlier TagMender (PREV) in a child process, on the same
    library and state folder; returns {"job", "counts", "repo"}. plan_only: a path - the plan
    is saved there (as `tagmender plan -o`), not run."""
    import json
    root = Path(root)
    spec = root / "earlier-spec.json"
    out = root / "earlier-out.json"
    spec.write_text(json.dumps({"root": str(root), "profile": profile_dict, "where": str(where),
                                "ident": {k: list(v) for k, v in (ident or {}).items()}, "out": str(out),
                                "target_extra": TARGET_EXTRA,
                                "plan_only": str(plan_only) if plan_only else None}))
    env = dict(os.environ, TM_REPO=str(repo or PREV))
    r = subprocess.run([sys.executable, "-c", _CHILD, str(_HERE), str(spec)], env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise AssertionError("the earlier version failed:\n" + r.stdout[-3000:] + r.stderr[-3000:])
    return json.loads(out.read_text())
