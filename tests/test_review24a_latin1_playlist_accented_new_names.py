# Twenty-fourth independent review (A). The review-22 fix (plan._cue_rewrites' as_sheet) lets a
# Latin-1 playlist beside an old Latin-1 rip ("01 H\xe9lls Bells.mp3" on disk, cp1252 text in
# the .m3u) be rewritten when a move renames the tracks. The NEW names are written into the
# sheet as text in the sheet's own encoding (cp1252) - but TagMender names the new files in
# UTF-8 on disk. Whenever a new name has a character outside ASCII - the usual case for such a
# rip: the accents are why its names were Latin-1 in the first place, and MusicBrainz gives the
# accented title - the playlist line is the cp1252 byte "\xe9" while the file is named with the
# UTF-8 bytes "\xc3\xa9": every renamed entry names a file that does not exist, exactly what
# the review-22 test reported (that test's new titles happened to be plain ASCII). A cue sheet
# of such a rip ("FILE ...") the same.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review24a_common import Case, encode, executor  # noqa: E402


class Latin1PlaylistAccented(Case):
    def go(self, album):
        d = self.lib / "AC-DC" / "1980 - Back in Black"
        fs = [encode(d / os.fsdecode(b"0%d H\xe9lls Bells %d.mp3" % (n, n)), 500 + 10 * n) for n in (1, 2)]
        self.ident.add(fs, album, "AC-DC")
        m3u = d / "Back in Black.m3u"
        m3u.write_bytes(b"01 H\xe9lls Bells 1.mp3\r\n02 H\xe9lls Bells 2.mp3\r\n")     # (the bytes on disk)
        prof = self.profile("move", naming={"preset": "artist-album-track", "template": "",
                                            "editions": "off", "album_editions": "off"})
        p = self.plan(prof)
        res = self.run_plan(p)
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = next(self.dest.rglob("*.m3u"))
        names = [os.fsdecode(x) for x in new.read_bytes().splitlines() if x.strip()]
        missing = [n for n in names if not (new.parent / n).exists()]
        self.assertEqual(missing, [], f"the moved playlist names files that are not there: {names!r}; "
                                      f"the folder has {sorted(x.name for x in new.parent.iterdir())!r}")

    def test_new_names_plain_ascii(self):          # (passes: the review-22 case)
        self.go("Hells Bells")

    def test_new_names_with_an_accent(self):
        self.go("Hélls Bells")


if __name__ == "__main__":
    unittest.main()
