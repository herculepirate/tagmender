# fuzz_crash.py's kill points for moves, unchanged moves, hard-linked moves and rollback between two
# bind mounts of one filesystem (rename and link give EXDEV there). Needs root (mount --bind).
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os, sys, shutil, subprocess
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests")); sys.path.insert(0, str(REPO / "tests" / "fuzz"))
import fuzz_crash as fc
import tagio, plan as pl
from test_executor import target


class FuzzBind(fc.Fuzz):
    def setUp(self):
        super().setUp()
        host = self.host = self.tmp / "host"
        (host / "downloads").mkdir(parents=True)
        (host / "music").mkdir()
        self.inbox, self.music = self.tmp / "downloads", self.tmp / "music"
        self.inbox.mkdir(); self.music.mkdir()
        self.mnts = []
        for src, mnt in ((host / "downloads", self.inbox), (host / "music", self.music)):
            subprocess.run(["mount", "--bind", str(src), str(mnt)], check=True)
            self.mnts.append(mnt)
        shutil.move(str(self.lib), str(self.inbox / "lib"))
        self.lib = self.inbox / "lib"
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1, self.f2 = self.album / "01 a.flac", self.album / "02 b.flac"
        self.dest = self.music

    def tearDown(self):
        for m in getattr(self, "mnts", []):
            subprocess.run(["umount", str(m)])
        self.mnts = []
        super().tearDown()

    def roots(self):
        return [self.lib, self.music]

    def prop(self, **kw):
        return self.proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)], **kw)

    def s_bmove(self):
        return pl.build([self.prop()], self.profile(operation="move", destination=str(self.music)))

    def s_bmove_nochange(self):
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((self.f1, t1), (self.f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        return pl.build([self.prop(targets=[t1, t2])], self.profile(operation="move", destination=str(self.music)))

    def s_brollback(self):
        p = pl.build([self.prop()], self.profile(operation="move", destination=str(self.music)))
        d2 = Path(p["albums"][0]["files"][1]["dst"])
        d2.parent.mkdir(parents=True)
        d2.write_bytes(b"squatter")
        return p

    SCEN = ["s_bmove", "s_bmove_nochange", "s_brollback"]


if __name__ == "__main__":
    import unittest
    unittest.main(defaultTest="FuzzBind")
