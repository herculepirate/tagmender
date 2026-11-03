# Twenty-fourth independent review (B). test_wma_totaltracks and test_m4a_vinyl_track_number
# fail with TM_REPO=prev too; test_wavpack_show passes with prev (a regression of the APEv2
# UNSUPPORTED_TAGS fix).
"""The naming script sees only the fields the file's format can store, as the format stores
them - not the track's metadata, which is what Picard names the file from.

Cause: plan.py builds the naming variables (`names = {**hidden, **merged, **rel_tags}`) from
`merged`, which is the file's tags after the write: `raw` is the target cut to what
tagio.holds() says the format has room for, and `named` is the changes after
tagio.stored()/_norm(), which drops an MP4 track number that is not a positive integer (and
its total with it) and APEv2's UNSUPPORTED_TAGS. Picard's File.make_filename runs the naming
script on the file's whole metadata (File.copy_metadata keeps every field; only _save leaves
out what the format cannot hold).

Picard 3.0.2, end to end on a real release (Track.update_file_metadata, MP4File/ASFFile/
WavPackFile _save, File.make_filename):
  * WMA (ASF has no total-tracks field), "...$num(%tracknumber%,$len(%totaltracks%)) %title%",
    10 tracks: "01 Speak to Me.wma"; TagMender "1 Speak to Me.wma" (%totaltracks% empty).
  * M4A, a vinyl numbering script $set(tracknumber,A%tracknumber%), "%album%/%tracknumber%.
    %title%": "Hello/A1. Hello.m4a"; TagMender "Hello/_ Hello.m4a".
  * WavPack, $set(show,My Show) and "%show%/%title%": "My Show/Hello.wv"; TagMender
    "Hello.wv" (show is not written to APEv2, as Picard - but Picard still names by it)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

ALBUM = c.TARGET["album"][0]


class NamingSeesOnlyStoredFields(unittest.TestCase):
    def test_wma_totaltracks(self):
        files = c.album(".wma", 1)
        _p, res, dest = c.move_run(files, "%album%/$num(%tracknumber%,$len(%totaltracks%)) %title%",
                                   extra={"totaltracks": ["12"]})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), [f"{ALBUM}/01 Track 1.wma"])

    def test_m4a_vinyl_track_number(self):
        files = c.album(".m4a", 1)
        _p, res, dest = c.move_run(files, "%album%/%tracknumber%. %title%", ["$set(tracknumber,A%tracknumber%)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), [f"{ALBUM}/A1. Track 1.m4a"])

    def test_wavpack_show(self):
        files = c.album(".wv", 1)
        _p, res, dest = c.move_run(files, "%show%/%title%", ["$set(show,My Show)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["My Show/Track 1.wv"])


if __name__ == "__main__":
    unittest.main()
