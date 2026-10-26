# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""M4A: a tagger script that zero-pads the track or disc number - one of the commonest Picard
scripts, $set(tracknumber,$num(%tracknumber%,2)) - makes every M4A album fail.

The plan keeps the script's text value ("01"); MP4 stores track and disc numbers as integers
in trkn / disk, so the file reads back "1", and the executor's read-back check rejects the
write: "track01.m4a: tag tracknumber did not read back as written; album put back as it was".
The same for a vinyl side number ($set(tracknumber,%_musicbrainz_tracknumber%) -> "A1"),
which Picard 3.0.2 (picard/formats/mp4.py _save: int() in try/except ValueError) simply does
not write to trkn.

Picard 3.0.2, end to end on the same release and script (Track.update_file_metadata, then
MP4File._save): trkn = (1, 10), disk = (1, 1) - the album is tagged. MP3/FLAC/Ogg work in
TagMender (the text "01" is stored as is)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402

import mutagen  # noqa: E402


class M4aPaddedNumbers(unittest.TestCase):
    def test_padded_tracknumber(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(tracknumber,$num(%tracknumber%,2))"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[1])["trkn"], [(2, 2)])

    def test_padded_discnumber(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(discnumber,$num(%discnumber%,2))"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0])["disk"], [(1, 1)])

    def test_vinyl_side_number(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(tracknumber,A%tracknumber%)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0])["\xa9nam"], ["Track 1"])


if __name__ == "__main__":
    unittest.main()
