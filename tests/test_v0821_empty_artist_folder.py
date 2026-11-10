#!/usr/bin/env python3
"""0.8.21: an artist folder opened inside the library is removed once a move empties it."""
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio  # noqa: E402


class OpenedArtistFolder(Base):
    def move(self, opened, src_dir):
        f1 = audio(src_dir / "01 a.flac", 440, album="Some Album", title="a")
        f2 = audio(src_dir / "02 b.flac", 550, album="Some Album", title="b")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(source_dir=str(src_dir), files=[str(f1), str(f2)])], prof,
                     extra_roots=[opened])                 # as the desktop app opening that folder
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})

    def test_an_emptied_artist_folder_in_the_library_goes(self):
        artist = self.lib / "Some Artist, Some Band"
        self.move(artist, artist / "2025 - Some Album")
        self.assertFalse(artist.exists(), "the empty artist folder was left behind")
        self.assertTrue(self.lib.is_dir())                 # the library's own folder stays

    def test_a_folder_opened_outside_the_library_stays(self):
        inbox = self.tmp / "inbox"
        self.move(inbox, inbox / "Some Album")
        self.assertFalse((inbox / "Some Album").exists())  # the album's folder goes
        self.assertTrue(inbox.is_dir())                    # the folder you opened stays

    def test_an_artist_folder_with_another_album_stays(self):
        artist = self.lib / "Some Artist, Some Band"
        audio(artist / "2020 - Other" / "01 x.flac", 660, album="Other", title="x")
        self.move(artist, artist / "2025 - Some Album")
        self.assertTrue((artist / "2020 - Other" / "01 x.flac").exists())
        self.assertFalse((artist / "2025 - Some Album").exists())


if __name__ == "__main__":
    unittest.main()
