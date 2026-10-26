# Fourteenth independent review (Picard code): this test failed before its fix.
"""tag_albums.backup_tags / restore_tags (the executor's undo for MP3): an ID3v2.3 tag is
backed up with ID3.save(v2_version=3) and restored with id3.save(v2_version=3), both with
mutagen's default v23_sep="/". A v2.3 tag that holds several values the way Mp3tag,
foobar2000 or mutagen (v23_sep=None) write them - NUL-separated - is therefore backed up
already joined: undo gives back "A/B" where the file had ["A", "B"]."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tag_albums as ta  # noqa: E402
import tagio  # noqa: E402


class UndoV23(unittest.TestCase):
    def test_restore_gives_back_the_values_as_they_were(self):
        from mutagen.id3 import ID3, TCON, TIT2, TPE1, TXXX
        tmp = Path(tempfile.mkdtemp())
        blobs = tmp / "blobs"
        blobs.mkdir()
        p = make(tmp / "a.mp3")
        i = ID3()
        i.add(TIT2(encoding=1, text=["Title"]))
        i.add(TPE1(encoding=1, text=["A", "B"]))
        i.add(TCON(encoding=1, text=["Rock", "Pop"]))
        i.add(TXXX(encoding=1, desc="ARTISTS", text=["A", "B"]))
        i.save(str(p), v2_version=3, v23_sep=None)            # NUL-separated values in v2.3
        before = tagio.read(p)
        self.assertEqual(before["artist"], ["A", "B"])
        rec = ta.backup_tags(p, blobs)
        tagio.write(p, {"title": ["Changed"]})
        ta.restore_tags(p, rec, blobs)
        self.assertEqual(tagio.read(p), before, "undo did not give the tags back as they were")


if __name__ == "__main__":
    unittest.main()
