# Fourteenth independent review (Picard code): this test failed before its fix.
"""tag_albums.restore_tags (the executor's undo for M4A): a freeform atom is restored with
MP4FreeForm(data, v["fmt"] or 1) - a data type of 0 (AtomDataType.IMPLICIT, as iTunes and
other taggers write binary freeform atoms) is falsy, so it comes back typed UTF-8: undo does
not give the file back as it was (players that read the atom by its type now see text)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tag_albums as ta  # noqa: E402
import tagio  # noqa: E402


class UndoMp4(unittest.TestCase):
    def test_implicit_freeform_keeps_its_type(self):
        from mutagen.mp4 import MP4, AtomDataType, MP4FreeForm
        tmp = Path(tempfile.mkdtemp())
        blobs = tmp / "blobs"
        blobs.mkdir()
        p = make(tmp / "a.m4a")
        m = MP4(str(p))
        m["----:com.example:BLOB"] = [MP4FreeForm(b"\x00\x01\x02\x03", dataformat=AtomDataType.IMPLICIT)]
        m["\xa9nam"] = ["Title"]
        m.save()
        rec = ta.backup_tags(p, blobs)
        tagio.write(p, {"title": ["Changed"]})
        ta.restore_tags(p, rec, blobs)
        v = MP4(str(p)).tags["----:com.example:BLOB"][0]
        self.assertEqual((bytes(v), v.dataformat), (b"\x00\x01\x02\x03", AtomDataType.IMPLICIT))


if __name__ == "__main__":
    unittest.main()
