# Eleventh independent review: this test failed before its fix.
# Round 11: an UNRESOLVED loose single in an artist folder beside the artist's album
# folders, moved to the unresolved holding folder: the artist folder's photo goes with it.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
_say = lambda *a, **k: None
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import executor, plan as pl  # noqa
from test_executor import Base, audio, target  # noqa


class UnresolvedLooseSingle(Base):
    def go(self, name, status="unresolved"):
        artist = self.album.parent                     # lib/Some Artist (holds "2001 - Some Album")
        (artist / name).write_bytes(b"the artist photo")
        s = audio(artist / "single.flac", 660, title="single", artist="Some Artist")
        hold = self.tmp / "unresolved"
        hold.mkdir()
        prof = self.profile(operation="move", destination=str(self.dest),
                            unresolved={"action": "move", "move_to": str(hold), "allow_partial": False})
        if status == "unresolved":
            prop = self.proposal(source_dir=str(artist), files=[str(s)], status="unresolved", targets=[],
                                 confidence=0, reason="no candidate")
        else:
            prop = self.proposal(source_dir=str(artist), files=[str(s)],
                                 targets=[target(1, "single", album="Single", artist="AC/DC")])
        p = pl.build([prop], prof)
        comps = [(c["src"], c["dst"]) for al in p["albums"] for c in al["companions"]]
        _say(status, comps, [(f["src"], f["dst"], f["op"]) for al in p["albums"] for f in al["files"]], [al.get("notes") for al in p["albums"]])
        res = executor.Executor(p).run()
        self.assertTrue((artist / name).exists(),
                        f"the artist folder's {name} left with a loose single: {comps} {res['counts']}")

    def test_unresolved_folder_jpg(self):
        self.go("folder.jpg")

    def test_unresolved_artist_jpg(self):
        self.go("artist.jpg")

    def test_identified_folder_named_differently(self):
        # folder "Some Artist", tag "AC/DC" stands for "AC-DC" vs "AC/DC", "Motorhead" vs "Motörhead"
        self.go("artist.jpg", status="identified")


if __name__ == "__main__":
    unittest.main()
