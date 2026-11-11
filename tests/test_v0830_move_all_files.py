# MOV-1 (docs/MATCHING_RULES.md): an album moved (or copied) takes EVERY file of its own folders
# along - not only the profile's companion extensions - so the folder it leaves is empty (Popol
# Vuh - Sing, for Song Drives Away the Wolves: its .flac.auCDtect report was left behind, and the
# old folder with it). A folder shared with another album's music, and an artist folder, keep
# their own files as before. Synthetic folders: no library.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class MOV1(unittest.TestCase):
    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="tm-mov1-")).resolve()
        self.dest = self.top / "lib" / "Popol Vuh" / "1993 - Sing"
        # (a short companion list, as yours)
        self.prof = pf.from_dict({"name": "t", "operation": "move", "destination": str(self.top / "lib"),
                                  "companions": [".cue", ".log", ".jpg", ".txt"]})

    def tearDown(self):
        shutil.rmtree(self.top, ignore_errors=True)

    def comps(self, src, tracks):
        moved = {str(t): str(self.dest / t.name) for t in tracks}
        out, why = pl._companions(src, moved, self.prof, everything=True)
        return {p.relative_to(src).as_posix() for p, _d in out}, why

    def test_MOV1_every_file_of_the_album_folder_goes(self):
        src = self.top / "in" / "Popol Vuh - Sing"
        tracks = [touch(src / "01 a.flac"), touch(src / "02 b.flac")]
        for n in ("cover.jpg", "rip.log", "Popol Vuh - Sing.flac.auCDtect", "notes.rtf", "disc.toc", "info.nfo",
                  "Scans/back.tif"):
            touch(src / n)
        got, why = self.comps(src, tracks)
        self.assertEqual(why, "")
        self.assertEqual(got, {"cover.jpg", "rip.log", "Popol Vuh - Sing.flac.auCDtect", "notes.rtf", "disc.toc",
                               "info.nfo", "Scans/back.tif"})

    def test_MOV1_disc_folders_take_every_file_too(self):
        src = self.top / "in" / "Album"
        tracks = [touch(src / "CD1" / "01 a.flac"), touch(src / "CD2" / "01 b.flac")]
        touch(src / "CD1" / "CD1.flac.auCDtect")
        touch(src / "CD2" / "dr.foo_dr")
        self.dest = self.top / "lib" / "Album"
        moved = {str(tracks[0]): str(self.dest / "Disc 01" / "01.flac"),
                 str(tracks[1]): str(self.dest / "Disc 02" / "01.flac")}
        out, _why = pl._companions(src, moved, self.prof, everything=True)
        self.assertEqual({p.relative_to(src).as_posix() for p, _d in out}, {"CD1/CD1.flac.auCDtect", "CD2/dr.foo_dr"})

    def test_MOV1_not_for_an_album_nothing_identified_or_a_root(self):
        """A downloads folder of loose tracks (an album nothing identified, or a folder named on
        the command line): only the companion types go, never its other files."""
        src = self.top / "Downloads"
        tracks = [touch(src / "01 a.flac"), touch(src / "02 b.flac")]
        for n in ("taxes.pdf", "setup.exe", "backup.zip", "cover.jpg"):
            touch(src / n)
        moved = {str(t): str(self.dest / t.name) for t in tracks}
        out, _why = pl._companions(src, moved, self.prof)                       # (everything off)
        self.assertEqual({p.name for p, _d in out}, {"cover.jpg"})
        out, _why = pl._companions(src, moved, self.prof, tracks_only=True, everything=True)
        self.assertNotIn("setup.exe", {p.name for p, _d in out})

    def test_MOV1_a_folder_shared_with_another_album_keeps_its_files(self):
        src = self.top / "in" / "Singles"
        mine = touch(src / "01 a.flac")
        touch(src / "02 other.flac")                                      # (another album's track, staying)
        touch(src / "folder.flac.auCDtect")
        touch(src / "01 a.lrc")
        got, why = self.comps(src, [mine])
        self.assertEqual(why, pl.SHARED_FOLDER_NOTE)
        self.assertNotIn("folder.flac.auCDtect", got)

    def test_MOV1_an_artist_folder_keeps_its_own_unlisted_files(self):
        band = self.top / "in" / "Popol Vuh"
        mine = [touch(band / "01 a.flac"), touch(band / "02 b.flac")]
        touch(band / "Other One" / "01 x.flac")
        touch(band / "Other Two" / "01 y.flac")
        touch(band / "biography.rtf")
        got, _why = self.comps(band, mine)
        self.assertNotIn("biography.rtf", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
