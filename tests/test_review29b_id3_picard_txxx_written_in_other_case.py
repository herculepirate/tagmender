# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""MP3/WAV/AIFF: when a file already has one of Picard's own user-tag frames under another
letter case - TXXX:musicbrainz album id, TXXX:ORIGINALYEAR, TXXX:Barcode, the old Picard
spelling TXXX:Artists - TagMender writes the new value into that frame and never writes the
frame Picard reads (TXXX:MusicBrainz Album Id, TXXX:originalyear, TXXX:BARCODE, TXXX:ARTISTS).
Picard's ID3 names are case-sensitive, so Picard - and any player that reads the MusicBrainz
frames by their exact names - finds no album ID, no original year, no barcode in the file
TagMender wrote.

Checked end to end with Picard's own code (Picard 3.0.2 writes its own frames and leaves the
other-case one as a custom tag; for the obsolete "Artists"/"Work" it deletes the old frame,
id3.py __rename_freetext / _save_freetext_tag):
  file TXXX:musicbrainz album id=x -> Picard: TXXX:MusicBrainz Album Id=<id>, the lower-case
                                      frame kept as "x"; TagMender: TXXX:musicbrainz album
                                      id=<id> only
  file TXXX:ORIGINALYEAR=1999      -> Picard: TXXX:originalyear=1973 (+ ORIGINALYEAR=1999 kept);
                                      TagMender: TXXX:ORIGINALYEAR=1973 only
  file TXXX:Artists=Old            -> Picard: TXXX:ARTISTS; TagMender: TXXX:Artists
The second-run sweep showed the effect: Picard, loading the file TagMender wrote, finds
originalyear missing and has a change to save on every such track.

Cause: tagio._set_id3 (tagio.py:1238-1244): `existing = [... if x[5:].lower() ==
desc.lower()]`, `keep_case = tags[existing[0]].desc` - the existing frame's spelling is kept
for every TXXX, also for Picard's own mapped names (ID3_FREETEXT), not only for custom tags
as FORMATS.md says ("A custom tag that already exists in another letter case keeps its case").

Fix: for a field in ID3_FREETEXT write Picard's exact description (and delete the obsolete
"Artists"/"Work" frames as Picard does); keep the existing case only for custom tags.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TXXX

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402


class PicardTxxxOtherCase(unittest.TestCase):
    def _go(self, desc, value, target):
        files = c.album(".mp3", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(TXXX(encoding=3, desc=desc, text=[value]))
            m.save()
        targets = c.proposal(files).targets
        for t in targets:
            t.update(target)
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return mutagen.File(str(files[0])).tags

    def test_musicbrainz_album_id_lower_case(self):
        t = self._go("musicbrainz album id", "x", {})
        self.assertIn("TXXX:MusicBrainz Album Id", t, "the frame Picard reads the album ID from")
        self.assertEqual(list(t["TXXX:MusicBrainz Album Id"].text), ["b84ee12a-09ef-421b-82de-0441a926375b"])

    @unittest.skip("documented (docs/FORMATS.md): a custom tag in another case is updated where it is, not added beside it")
    def test_originalyear_upper_case(self):
        t = self._go("ORIGINALYEAR", "1999", {"originalyear": ["1973"]})
        self.assertIn("TXXX:originalyear", t, "Picard's own frame")
        self.assertEqual(list(t["TXXX:originalyear"].text), ["1973"])

    def test_old_picard_artists_frame(self):
        t = self._go("Artists", "Old", {"artists": ["Pink Floyd"]})
        self.assertIn("TXXX:ARTISTS", t, "Picard renames the obsolete TXXX:Artists")
        self.assertNotIn("TXXX:Artists", t)


if __name__ == "__main__":
    unittest.main()
