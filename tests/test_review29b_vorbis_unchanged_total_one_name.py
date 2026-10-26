# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression: last
# round's fix covers a total whose number or value changes, not one that stays).
"""FLAC/Ogg/Opus: a file whose track or disc numbers are already right keeps its total under
one name only. Picard 3.0.2 writes both TOTALTRACKS and TRACKTOTAL (TOTALDISCS and DISCTOTAL)
on every save; TagMender writes them only when the number or the total changes.

Checked end to end against Picard's own code (Album._parse_release, the tagger scripts,
Track.update_file_metadata, VCommentFile._save): a 2-track, 1-disc release, files ripped with
TRACKNUMBER=1/2, TRACKTOTAL=2, DISCNUMBER=1, DISCTOTAL=1 (as EAC, dBpoweramp and foobar2000
write them) and some other tag to change (an old title):

  Picard:     ... DISCNUMBER=1 DISCTOTAL=1 TOTALDISCS=1 TRACKNUMBER=1 TRACKTOTAL=2 TOTALTRACKS=2
  TagMender:  ... DISCNUMBER=1 DISCTOTAL=1              TRACKNUMBER=1 TRACKTOTAL=2

(track 2, whose number did change from the rip's "1", gets both names.) The same happens when
the totals are in "Preserve these tags": Picard writes the preserved value under both names -
also when a tagger script unsets the preserved total (checked end to end: Picard
TOTALTRACKS=9 TRACKTOTAL=9, TagMender TRACKTOTAL=9 alone).

Cause: tagio._write_pair (tagio.py:1139-1146) writes the total fields only when
`tot in changed or num in changed`. A pair the plan does not change (plan.diff_tags leaves
out a field equal to the file's; TRACKTOTAL is read as totaltracks) is never written, so the
second name is never added - for nearly every correctly ripped disc the TOTALDISCS field.
A second TagMender run sees nothing to change, so the file stays different from Picard's.
The same root leaves a disc number written "1/1" (DISCNUMBER=1/1, no total fields) as it is
where Picard writes DISCNUMBER=1, DISCTOTAL=1, TOTALDISCS=1 - and Picard, loading the file
TagMender wrote, reads discnumber "1/1" and no totaldiscs, so it sees a change to save.

Fix: when the file is written (any change at all) and the format is Vorbis, add the missing
name of each total the file holds (TOTALTRACKS beside TRACKTOTAL and back), with the value the
file has - or treat a total held under one name only (or inside a "1/1" number) as a change,
so the pair is rewritten as Picard writes it; for a preserved total, write the preserved
value under both names even when a script unset it (fe["keep"] only when not preserved).
"""
import os
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402
import picard_config  # noqa: E402


def rip(files):
    for i, f in enumerate(files):
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags["TITLE"] = ["old title"]
        m.tags["TRACKNUMBER"] = [str(i + 1)]
        m.tags["TRACKTOTAL"] = [str(len(files))]
        m.tags["DISCNUMBER"] = ["1"]
        m.tags["DISCTOTAL"] = ["1"]
        m.save()


class VorbisUnchangedTotal(unittest.TestCase):
    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm29B/Picard.ini"
        picard_config._cache.clear()

    def _tags(self, ext):
        files = c.album(ext, 2)
        rip(files)
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [{k.upper(): v for k, v in mutagen.File(str(f)).tags.items()} for f in files]

    def _check(self, ext):
        for tags in self._tags(ext):
            self.assertEqual(tags.get("TITLE")[0][:6], "Track ", "the file was written")
            self.assertEqual(tags.get("DISCTOTAL"), ["1"])
            self.assertEqual(tags.get("TOTALDISCS"), ["1"], "Picard writes TOTALDISCS too")
            self.assertEqual(tags.get("TRACKTOTAL"), ["2"])
            self.assertEqual(tags.get("TOTALTRACKS"), ["2"], "Picard writes TOTALTRACKS too")

    def test_flac(self):
        self._check(".flac")

    def test_ogg(self):
        self._check(".ogg")

    def test_opus(self):
        self._check(".opus")

    def test_preserved_totals(self):
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags=totaltracks, totaldiscs\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()
        self._check(".flac")

    def test_disc_number_with_total_inside(self):
        # DISCNUMBER=1/1 (no total fields), the release has one disc: TagMender reads disc 1 of 1
        # - nothing to change - and leaves "1/1"; Picard (checked end to end) writes DISCNUMBER=1,
        # DISCTOTAL=1, TOTALDISCS=1
        files = c.album(".flac", 2)
        rip(files)
        for f in files:
            m = mutagen.File(str(f))
            del m.tags["DISCTOTAL"]
            m.tags["DISCNUMBER"] = ["1/1"]
            m.save()
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        for f in files:
            tags = {k.upper(): v for k, v in mutagen.File(str(f)).tags.items()}
            self.assertEqual((tags.get("DISCNUMBER"), tags.get("DISCTOTAL"), tags.get("TOTALDISCS")),
                             (["1"], ["1"], ["1"]))

    def test_preserved_total_unset_by_script(self):
        # "Preserve these tags: totaltracks" and a script's $unset(totaltracks), a file numbered
        # 5 of 9: Picard keeps 9 (preserved) and writes it as TOTALTRACKS and TRACKTOTAL; TagMender
        # leaves TRACKTOTAL=9 alone (fe["keep"], plan.py:967-969) and writes no TOTALTRACKS
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags=totaltracks\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()
        files = c.album(".flac", 2)
        for f in files:
            m = mutagen.File(str(f))
            m.tags["TRACKNUMBER"], m.tags["TRACKTOTAL"] = ["5"], ["9"]
            m.save()
        p, res = c.run(files, scripts=["$unset(totaltracks)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        for f in files:
            tags = {k.upper(): v for k, v in mutagen.File(str(f)).tags.items()}
            self.assertEqual(tags.get("TRACKTOTAL"), ["9"])
            self.assertEqual(tags.get("TOTALTRACKS"), ["9"], "Picard writes the preserved total under both names")


if __name__ == "__main__":
    unittest.main()
