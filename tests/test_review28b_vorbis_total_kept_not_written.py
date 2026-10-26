"""FLAC/Ogg: a total that does not change is no longer written as Picard writes it. A file
holding the right total in TRACKTOTAL (or DISCTOTAL) alone - as EAC, dBpoweramp, Mp3tag and
others write it - keeps only that field; Picard 3.0.2 writes both TOTALTRACKS and TRACKTOTAL
(TOTALDISCS and DISCTOTAL) on every save.

Last round's change to tagio.py:1104-1120 (_write_pair, Vorbis) writes a total only when it
is among the changes (or was inside "5/9"). Picard's VCommentFile._save
(picard/formats/vorbis.py:325-352) writes every metadata field - totaltracks as TOTALTRACKS
- and adds TRACKTOTAL; a total equal to the file's (the release has as many tracks as the
file says, or the total is in "Preserve these tags") is written all the same. Checked end to
end with Picard's code: TagMender now lacks TOTALTRACKS / TOTALDISCS on every track (before
last round it rewrote both names whenever the track or disc number changed, as Picard does).
Players and tools that read only TOTALTRACKS/TOTALDISCS (foobar2000's own field names) show
no total.

Regression: passes with TM_REPO=prev.
"""
import os
import unittest

import mutagen

import _review28b_common as c
import picard_config


def pretag(path, **fields):
    f = mutagen.File(str(path))
    for k, v in fields.items():
        f.tags[k] = [v]
    f.save()


class VorbisTotalKept(unittest.TestCase):
    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm28B/Picard.ini"
        picard_config._cache.clear()

    def _keys(self, ext, **fields):
        files = c.album(ext, 2)
        for f in files:
            pretag(f, **fields)
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [{k.upper(): v for k, v in mutagen.File(str(f)).tags.items()} for f in files]

    def test_flac_track_total_same_as_release(self):
        # a 2-track album whose files say TRACKNUMBER=5, TRACKTOTAL=2: renumbered 1, 2 of 2
        for tags in self._keys(".flac", TRACKNUMBER="5", TRACKTOTAL="2"):
            self.assertEqual(tags.get("TRACKTOTAL"), ["2"])
            self.assertEqual(tags.get("TOTALTRACKS"), ["2"], "Picard writes TOTALTRACKS too")

    def test_ogg_disc_total_same_as_release(self):
        for tags in self._keys(".ogg", DISCNUMBER="2", DISCTOTAL="1"):
            self.assertEqual(tags.get("DISCTOTAL"), ["1"])
            self.assertEqual(tags.get("TOTALDISCS"), ["1"], "Picard writes TOTALDISCS too")

    def test_flac_preserved_total(self):
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags=totaltracks\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()
        for tags in self._keys(".flac", TRACKNUMBER="5", TRACKTOTAL="9"):
            self.assertEqual(tags.get("TRACKTOTAL"), ["9"])
            self.assertEqual(tags.get("TOTALTRACKS"), ["9"], "Picard writes the preserved total as TOTALTRACKS too")


if __name__ == "__main__":
    unittest.main()
