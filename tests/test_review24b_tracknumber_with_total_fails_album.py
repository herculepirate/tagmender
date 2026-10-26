# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
# Fixed: the album is tagged; the number and its total are written as such (Picard writes the
# text as it is - "1/2/2" in ID3, which it cannot read back itself; docs/PICARD_COVERAGE.md).
"""A tagger script that writes the track number as "N/total" - $set(tracknumber,
%tracknumber%/%totaltracks%), what users do to get "01/12" in players that show TRACKNUMBER
as is - makes the whole album fail in MP3, FLAC, Ogg, WavPack and WAV:

  "track01.flac: tag tracknumber did not read back as written; album put back as it was"

Cause: the plan keeps the script's value "1/2" for tracknumber; tagio.read() splits a
"1/2" track number into tracknumber "1" + totaltracks "2", so the executor's read-back check
(executor.py:1124-1126 and 1183-1185, `if (got.get(k) or []) != (v or [])` after tagio.write) sees "1" != "1/2" and
rolls the album back. Nothing is tagged or moved.

Picard 3.0.2, end to end on the same release and script (Track.update_file_metadata, then
the format's _save): FLAC/Ogg TRACKNUMBER=1/2 + TOTALTRACKS=2; MP3 and WAV TRCK "1/2/2";
WavPack Track "1/2/2". The album is tagged.

The same for a disc number written "1/2" ($set(discnumber,1/2): Picard
FLAC DISCNUMBER=1/2 + TOTALDISCS=1 + DISCTOTAL=1, MP3 TPOS "1/2/1"; TagMender fails the album in MP3, FLAC,
Ogg, WavPack, WMA and WAV), and for a track number a script leaves with spaces around it
($set(tracknumber, 3 ) - file-level scripts are not stripped: Picard MP3 TRCK " 3 /2";
TagMender fails MP3, WavPack and WAV)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

import mutagen  # noqa: E402

SCRIPT = "$set(tracknumber,%tracknumber%/%totaltracks%)"


class TrackNumberWithTotal(unittest.TestCase):
    def test_flac(self):
        files = c.album(".flac")
        _p, res = c.run(files, [SCRIPT])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        f = mutagen.File(files[1])
        self.assertEqual(f["TRACKNUMBER"], ["2"])
        self.assertEqual(f["TOTALTRACKS"], ["2"])

    def test_mp3(self):
        files = c.album(".mp3")
        _p, res = c.run(files, [SCRIPT])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[1])["TRCK"].text, ["2/2"])

    def test_discnumber_with_total_flac(self):
        files = c.album(".flac")
        _p, res = c.run(files, ["$set(discnumber,%discnumber%/2)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0])["DISCNUMBER"], ["1"])
        self.assertEqual(mutagen.File(files[0])["TOTALDISCS"], ["1"])      # (the release's total stays)

    def test_tracknumber_with_spaces_mp3(self):
        files = c.album(".mp3")
        _p, res = c.run(files, ["$set(tracknumber, 3 )"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0])["TRCK"].text, ["3/2"])


if __name__ == "__main__":
    unittest.main()
