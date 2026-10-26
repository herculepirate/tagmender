# Shared setup for the review-16 A tests: temp XDG dirs before any TagMender import, the repo
# (TM_REPO, else the snapshot) on sys.path, and helpers to make MP3 files with odd tag layouts
# and to run a plan, check that a second plan settles, and undo it.
import os
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm16A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm16A-state-")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _review13b_common import make  # noqa: E402,F401
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tag_albums as ta  # noqa: E402
from test_executor import Base, target  # noqa: E402

for _n in dir(Base):
    if _n.startswith("test_"):
        setattr(Base, _n, None)

# an ID3v1.1 tag: title, artist, album, year, comment, track 1, genre 17 (Rock)
V1 = (b"TAG" + b"V1 title".ljust(30, b"\0") + b"V1 artist".ljust(30, b"\0") + b"V1 album".ljust(30, b"\0")
      + b"1999" + b"Ripped by me".ljust(28, b"\0") + b"\0\x01" + b"\x11")


def lyrics3():
    body = b"LYRICSBEGIN" + b"IND00002" + b"10" + b"LYR00005" + b"hello"
    return body + str(len(body)).rjust(6, "0").encode() + b"LYRICS200"


def bare_mp3(path, freq=440):
    """An MP3 with no ID3 tag at all."""
    from mutagen.id3 import ID3
    path = make(path, freq)
    try:
        ID3(str(path)).delete(str(path))
    except Exception:
        pass
    return path


def add_ape(path, **items):
    from mutagen.apev2 import APEv2
    a = APEv2()
    for k, v in (items or {"Title": "Old", "Custom": "kept by the player"}).items():
        a[k] = v
    a.save(str(path))


def append(path, data):
    with open(path, "ab") as fh:
        fh.write(data)


def state(f):
    """The file's tags as undo must put them back (TagMender's own record of them)."""
    d = tempfile.mkdtemp()
    rec = ta.backup_tags(f, d)
    return executor._rec_key(rec), {x.name: x.read_bytes() for x in Path(d).iterdir()}


class Run(Base):
    def setUp(self):
        super().setUp()
        self.alb = self.lib / "Artist" / "2001 - Album"

    def prop(self, **extra):
        return self.proposal(source_dir=str(self.alb), files=[str(f) for f in self.fs],
                             targets=[target(1, "First", **extra), target(2, "Second", **extra)])

    def run_plan(self, picard, **extra):
        prof = self.profile(follow_picard=False, picard=picard)
        before = {f: state(f) for f in self.fs}
        p = pl.build([self.prop(**extra)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, [a.get("detail") for a in res["albums"]])
        return p, res, before, prof
