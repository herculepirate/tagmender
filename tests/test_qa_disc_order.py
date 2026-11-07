"""Two discs' files in one folder without disc tags (an EAC rip of each disc, the album tags
"Title CD 1" and "Title CD 2", tracks 1 to 11 twice): each file's disc is read from its album
tag, so the discs do not interleave and each file is lined up with its own track of the
release. When nothing says which disc a file is on, the album is never matched on its own:
its files would be given other tracks' IDs. Synthetic files in temporary folders."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
FFMPEG = shutil.which("ffmpeg")


def album(tracks, **kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1, "tracks": tracks,
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(tracks, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "CA", "label": "L", "catnos": set(), "barcodes": set(),
         "disc_count": 2, "tracks": tracks}
    c.update(kw)
    return c


class AlbumTagNamesTheDisc(unittest.TestCase):
    def info(self, album, disc=0):
        return {"album": album, "disc": disc}

    def test_the_forms_a_ripper_writes(self):
        for one, two, title in (("Doubt (Compilation) CD 1", "Doubt (Compilation) CD 2", "Doubt (Compilation)"),
                                ("Doubt (Disc 1)", "Doubt (Disc 2)", "Doubt"),
                                ("Doubt [CD1]", "Doubt [CD2]", "Doubt"),
                                ("Doubt - Disc 1 of 2", "Doubt - Disc 2 of 2", "Doubt")):
            got, base = ma.album_tag_discs([("a", self.info(one)), ("b", self.info(two))])
            self.assertEqual((got, base), ({"a": 1, "b": 2}, title), one)

    def test_only_when_every_file_says_it_and_they_name_one_album(self):
        none = ({}, "")
        self.assertEqual(ma.album_tag_discs([("a", self.info("Doubt CD 1")), ("b", self.info("Doubt"))]), none)
        self.assertEqual(ma.album_tag_discs([("a", self.info("Doubt CD 1")), ("b", self.info("Other CD 2"))]), none)
        self.assertEqual(ma.album_tag_discs([("a", self.info("Doubt CD 1")), ("b", self.info("Doubt CD 1"))]), none)
        self.assertEqual(ma.album_tag_discs([("a", self.info("Greatest Hits 1")), ("b", self.info("Greatest Hits 2"))]),
                         none)                                    # (a number alone is no disc)
        self.assertEqual(ma.album_tag_discs([("a", self.info("CD 1")), ("b", self.info("CD 2"))]), none)
        # (a disc tag wins: only the files without one are read this way)
        self.assertEqual(ma.album_tag_discs([("a", self.info("Doubt CD 1", disc=1)), ("b", self.info("Doubt CD 2"))]),
                         none)


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class ReadAlbum(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-discs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        from test_executor import audio
        self.dir = self.tmp / "2005 - Doubt"
        self.audio = audio

    def make(self, album_of):
        # the files sort by number first: 01 B-one (disc 2) before 01 Z-one (disc 1)
        names = {(1, 1): "Z-one", (1, 2): "Y-two", (2, 1): "B-one", (2, 2): "A-two"}
        for (disc, num), title in names.items():
            self.audio(self.dir / f"{num:02d} - Band - {title}.flac", 300 + 100 * disc + 10 * num,
                       album=album_of(disc), title=title, tracknumber=f"{num:02d}", artist="Band")

    def test_each_file_on_the_disc_its_album_tag_names(self):
        self.make(lambda disc: f"Doubt (Compilation) CD {disc}")
        a = ma.read_album(self.dir, "Band", self.dir.name)
        self.assertEqual([(t["disc"], t["num"], t["title"]) for t in a["tracks"]],
                         [(1, 1, "Z-one"), (1, 2, "Y-two"), (2, 1, "B-one"), (2, 2, "A-two")])
        self.assertEqual((a["discs"], a["title"]), (2, "Doubt (Compilation)"))
        self.assertEqual(mc.numbers_repeat(a), 0)

    def test_without_it_the_numbers_repeat_and_say_so(self):
        self.make(lambda disc: "Doubt")
        a = ma.read_album(self.dir, "Band", self.dir.name)
        self.assertEqual(a["discs"], 1)
        self.assertEqual(mc.numbers_repeat(a), 4)


class RepeatedNumbersAreNeverMatchedAlone(unittest.TestCase):
    """The order of files that share a disc and track number against the release is a guess:
    the album goes to review, whatever proves the release (a catalogue number excuses lengths
    that disagree, never this)."""

    def tracks(self):
        files = [{"disc": 1, "num": n, "title": t, "secs": s} for n, t, s in
                 ((1, "Innocent", 311.0), (1, "Sphere Eclipse", 367.0), (2, "Transgressor", 206.0),
                  (2, "Without Honour", 229.0))]
        rel = [{"disc": d, "title": t, "secs": s} for d, t, s in
               ((1, "Sphere Eclipse", 367), (1, "Transgressor", 206), (2, "Innocent", 311),
                (2, "Without Honour", 229))]
        return files, rel

    def test_review_with_the_reason(self):
        files, rel = self.tracks()
        r = mc.classify([mc.score_candidate(album(files), cand(rel), CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(any("share their track numbers" in w for w in r["why"]), r["why"])

    def test_a_catalogue_number_does_not_excuse_it(self):
        files, rel = self.tracks()
        r = mc.classify([mc.score_candidate(album(files, catnos={"abc123"}), cand(rel, catnos={"abc123"}), CFG)],
                        CFG)
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(any("share their track numbers" in w for w in r["why"]), r["why"])

    def test_the_same_files_on_their_discs_are_matched(self):
        files, rel = self.tracks()
        on_discs = [dict(f, disc=d, num=n) for f, (d, n) in zip(
            files, ((2, 1), (1, 1), (1, 2), (2, 2)))]
        on_discs.sort(key=lambda t: (t["disc"], t["num"]))
        r = mc.classify([mc.score_candidate(album(on_discs, discs=2), cand(rel), CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])


if __name__ == "__main__":
    unittest.main()
