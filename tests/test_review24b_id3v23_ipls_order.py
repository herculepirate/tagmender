# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""ID3v2.3 (write_id3v23 on): the involved-people list (IPLS) TagMender writes puts every
performer after all the producer / arranger / engineer / mixer credits; Picard 3.0.2 writes
them in the track's metadata order, interleaved.

Cause: tagio._set_id3_people (tagio.py:1170) `if v23: tipl, tmcl = tipl + tmcl, []` -
the TIPL roles first, then the performers. Picard's ID3File._save puts a performer straight
into the 'tipl' people list when write_id3v23 is on (_save_performer_tag: "TIPL will be
upgraded to IPLS"), in metadata.rawitems() order, the same list as _save_rtipl_role.

A tagger script $set(producer,P)$set(performer:guitar,Joe)$set(arranger,Arr), MP3, v2.3:
Picard IPLS [producer P, guitar Joe, arranger Arr]; TagMender [producer P, arranger Arr,
guitar Joe] (confirmed end to end with Picard's own code; with MusicBrainz track
relationships alone the orders happened to agree)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

from mutagen.id3 import ID3  # noqa: E402


class Id3v23IplsOrder(unittest.TestCase):
    def test_order_follows_the_metadata(self):
        files = c.album(".mp3", 1)
        _p, res = c.run(files, ["$set(producer,P)$set(performer:guitar,Joe)$set(arranger,Arr)"],
                        picard={"write_id3v23": True, "id3v23_join_with": "; "})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        tags = ID3(str(files[0]))
        self.assertEqual(tags.version[:2], (2, 3))
        self.assertEqual([list(p) for p in tags.getall("TIPL")[0].people],
                         [["producer", "P"], ["guitar", "Joe"], ["arranger", "Arr"]])


if __name__ == "__main__":
    unittest.main()
