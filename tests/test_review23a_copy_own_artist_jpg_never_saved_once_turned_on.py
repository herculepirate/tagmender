# Twenty-third independent review (A): artist.jpg in the artist folder is off by default now.
# A user who copies a library with the default, then ticks "Also save it as artist.jpg in the
# artist folder" and runs again, gets an artist.jpg in the copies' artist folder only for the
# artists TagMender downloads a photo for - never for the artists whose own artist.jpg is in
# the source's artist folder (the photo a first run with the setting on does copy there).
# With a move profile it is worse: the moved albums' artist folder gets a downloaded photo,
# which is not the user's own photo the job put into the tracks (a stranger's face, perhaps).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review23a_common import Case, jpeg, pl  # noqa: E402


class CopyOwnArtistPhoto(Case):
    def build(self):
        self.album(self.lib / "Artist A" / "2001 - First", "First", "Artist A")
        self.album(self.lib / "Artist A" / "2003 - Second", "Second", "Artist A")
        jpeg(self.lib / "Artist A" / "artist.jpg", (200, 0, 0), (700, 700))      # the user's own photo
        self.album(self.lib / "Artist B" / "2005 - Third", "Third", "Artist B")  # no photo: a download

    def test_fresh_run_with_the_setting_on_copies_it(self):
        # (the reference: what the setting does on a first run)
        self.build()
        self.run_plan(self.plan(self.artist_profile("copy", "artist.jpg")))
        self.assertTrue((self.dest / "Artist A" / "artist.jpg").exists())
        self.assertTrue((self.dest / "Artist B" / "artist.jpg").exists())

    def test_turned_on_after_a_first_run_with_the_default(self):
        self.build()
        self.run_plan(self.plan(self.artist_profile("copy", "")))               # the new default
        self.assertFalse((self.dest / "Artist A" / "artist.jpg").exists())
        on = self.artist_profile("copy", "artist.jpg")
        p = self.plan(on)
        self.run_plan(p)
        self.assertTrue((self.dest / "Artist B" / "artist.jpg").exists())       # (the download: saved)
        self.assertTrue((self.dest / "Artist A" / "artist.jpg").exists(),
                        "the user's own artist.jpg is never saved in the copies' artist folder once the "
                        "setting is turned on:\n" + pl.preview(p))
        self.assertEqual((self.dest / "Artist A" / "artist.jpg").read_bytes(),
                         (self.lib / "Artist A" / "artist.jpg").read_bytes())


class MoveOwnArtistPhoto(Case):
    def build(self):
        self.album(self.lib / "Artist A" / "2001 - First", "First", "Artist A")
        self.mine = jpeg(self.lib / "Artist A" / "artist.jpg", (200, 0, 0), (700, 700)).read_bytes()

    def test_fresh_run_with_the_setting_on_takes_it_along(self):
        self.build()
        self.run_plan(self.plan(self.artist_profile("move", "artist.jpg")))
        self.assertEqual((self.dest / "Artist A" / "artist.jpg").read_bytes(), self.mine)

    def test_turned_on_after_a_first_run_with_the_default(self):
        self.build()
        self.run_plan(self.plan(self.artist_profile("move", "")))
        p = self.plan(self.artist_profile("move", "artist.jpg"), self.dest)
        self.run_plan(p)
        got = self.dest / "Artist A" / "artist.jpg"
        self.assertTrue(got.exists())
        self.assertEqual(got.read_bytes(), self.mine,
                         "the artist folder got a downloaded photo, not the user's own one that is in the "
                         "tracks:\n" + pl.preview(p))


if __name__ == "__main__":
    unittest.main()
