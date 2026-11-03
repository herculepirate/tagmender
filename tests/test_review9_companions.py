# Ninth independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import shutil
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


def groups(prof, path):
    return eng_mod.Engine.albums_in(type("E", (), {"profile": prof})(), path)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


class B(Base):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib / "Some Artist")

    def move_main(self, m, main_files):
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(m), files=[str(f) for f in main_files], release_id="main",
                             targets=[target(n + 1, f"T{n}", album="Main", artist="Band",
                                             **({"discnumber": str(1 + n // 2)} if len(main_files) > 2 else {}))
                                      for n in range(len(main_files))])
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        return p


class NestedLiveAlbum(B):
    """Main holds its own tracks and a nested album that is not bonus-named ("Live 1995",
    "EP", "Singles"): before fcfd152 Main's cover/log/cue moved with it."""
    def test_album_takes_its_cover_and_log(self):
        m = self.lib / "Band" / "Main"
        a = [audio(m / "01 a.flac", 500, album="Main", albumartist="Band"),
             audio(m / "02 b.flac", 510, album="Main", albumartist="Band")]
        live = m / "Live 1995"
        audio(live / "01 x.flac", 600, album="Live 1995", albumartist="Band")
        audio(live / "02 y.flac", 610, album="Live 1995", albumartist="Band")
        (m / "cover.jpg").write_bytes(b"main cover")
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"Main's own companions stayed behind: {left}")


class DiscSetWithBonus(B):
    """Main/CD1 + Main/CD2 (the album) + Main/Bonus (album "Main (Bonus)"): 1c028a3 says an
    album folder with a bonus album inside takes its cover along - not when its music is in
    disc folders (src_dir not in `ours`)."""
    def test_disc_set_takes_its_cover(self):
        m = self.lib / "Band" / "Main"
        a = [audio(m / "CD1" / "01 a.flac", 500, album="Main", albumartist="Band", discnumber="1"),
             audio(m / "CD1" / "02 b.flac", 510, album="Main", albumartist="Band", discnumber="1"),
             audio(m / "CD2" / "01 c.flac", 520, album="Main", albumartist="Band", discnumber="2"),
             audio(m / "CD2" / "02 d.flac", 530, album="Main", albumartist="Band", discnumber="2")]
        bonus = m / "Bonus"
        audio(bonus / "01 x.flac", 600, album="Main (Bonus)", albumartist="Band")
        (m / "cover.jpg").write_bytes(b"main cover")
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"the disc set's own cover/log stayed behind with the bonus: {left}")


if __name__ == "__main__":
    unittest.main()
