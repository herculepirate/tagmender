# MED-20: a MusicBrainz release's download ("Digital Media": a bonus track's code) is no disc of
# it. At the end of the release, none of its tracks your files, it is not counted in the disc
# total (Helge Lien Trio - Guzuguzu: CD + Digital Media; a CD rip is "1 of 1", not "1 of 2").
# A record of a CD box still counts (MED-19); a rip holding the download's track keeps the
# count. Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0863_audio_disc_total import mb_box, numbers  # noqa: E402
import engine  # noqa: E402
import mb_tags  # noqa: E402


class MED20_DigitalMedia(unittest.TestCase):
    def test_MED20_a_cd_and_its_download_counts_one(self):
        rel = mb_box(["CD", "Digital Media"])
        self.assertEqual(engine._unheld_digital_media(rel, [0, 1]), {2})
        tags = mb_tags.release_tags(rel, {}, uncounted={2})
        self.assertEqual(numbers(tags[:2]), [("1", "1")] * 2)

    def test_MED20_without_it_the_download_counted(self):
        # (what was written before: "1 of 2")
        self.assertEqual(numbers(mb_tags.release_tags(mb_box(["CD", "Digital Media"]), {})[:2]), [("1", "2")] * 2)

    def test_MED20_your_files_holding_the_downloads_track_keep_the_count(self):
        rel = mb_box(["CD", "Digital Media"])
        self.assertEqual(engine._unheld_digital_media(rel, [0, 1, 2]), set())

    def test_MED20_only_a_download_at_the_end(self):
        # (a download between discs keeps the discs' numbers: never a number above the total)
        self.assertEqual(engine._unheld_digital_media(mb_box(["CD", "Digital Media", "CD"]), [0, 1, 4, 5]), set())

    def test_MED20_a_release_all_download_keeps_its_count(self):
        self.assertEqual(engine._unheld_digital_media(mb_box(["Digital Media"]), [0]), set())
        self.assertEqual(engine._unheld_digital_media(mb_box(["Digital Media", "Digital Media"]), [0, 1]), set())

    def test_MED20_a_record_is_a_disc(self):
        # (MED-19: a CD box's record is an audio disc and still counts; never MED-20's)
        self.assertEqual(engine._unheld_digital_media(mb_box(["CD", '12" Vinyl']), [0, 1]), set())
        self.assertEqual({t["totaldiscs"][0] for t in mb_tags.release_tags(mb_box(["CD", '12" Vinyl']), {})}, {"2"})

    def test_MED20_two_cds_and_a_download(self):
        rel = mb_box(["CD", "CD", "Digital Media"])
        self.assertEqual(engine._unheld_digital_media(rel, [0, 1, 2, 3]), {3})
        tags = mb_tags.release_tags(rel, {}, uncounted={3})
        self.assertEqual(numbers(tags[:4]), [("1", "2")] * 2 + [("2", "2")] * 2)

    def test_MED20_a_video_disc_beside_the_download_counts_out_too(self):
        # (review: MED-17's trailing video disc and the download, either order: "1 of 1")
        for formats in (["CD", "DVD-Video", "Digital Media"], ["CD", "Digital Media", "DVD-Video"]):
            rel = mb_box(formats)
            digital = engine._unheld_digital_media(rel, [0, 1])
            self.assertEqual(digital, {formats.index("Digital Media") + 1}, formats)
            tags = mb_tags.release_tags(rel, {}, uncounted=digital)
            self.assertEqual(numbers(tags[:2]), [("1", "1")] * 2, formats)

    def test_MED20_discogs_leading_its_musicbrainz_twin_counts_one(self):
        # (review: Discogs's pick leads, MusicBrainz's CD + Digital Media entry its twin, your
        # files the CD's tracks: the twin's tags "1 of 1" as Discogs's own count)
        rel = mb_box(["CD", "Digital Media"])
        rel.update(date="2017", status="Official")
        tracks = [{"disc": 1, "num": i + 1, "title": f"M1 {i}", "secs": s}
                  for i, s in enumerate((10.0, 20.0))]
        album = {"artist": "Band V", "title": "Box V", "year": 2017, "discs": 1, "tracks": tracks}
        full = mb_tags.release_tags(rel, {})
        out = engine.Engine._twin_slots(album, rel, full, {"source": "discogs"}, 2, {})
        self.assertEqual(numbers(out), [("1", "1")] * 2)


if __name__ == "__main__":
    unittest.main()
