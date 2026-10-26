# Fourteenth independent review (Picard code): this test failed before its fix.
"""tagio.write() on an ID3v2.3 tag changes ONLY the fields named - its docstring's promise.
But save_id3() saves the whole tag with v23_sep="/", so every untouched frame holding
several NUL-separated values (as Mp3tag, foobar2000 or mutagen write v2.3) is rewritten as
one "/"-joined value: changing the title turns ARTISTS ["A", "B"] into ["A/B"] and the
genres ["Rock", "Pop"] into ["Rock/Pop"]. The plan showed a title change only, and the
executor's read-back check looks at the changed fields only, so nothing notices."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tagio  # noqa: E402


class OtherFramesKept(unittest.TestCase):
    def test_title_change_leaves_other_values_alone(self):
        from mutagen.id3 import ID3, TCON, TIT2, TXXX
        p = make(Path(tempfile.mkdtemp()) / "a.mp3")
        i = ID3()
        i.add(TIT2(encoding=1, text=["Title"]))
        i.add(TCON(encoding=1, text=["Rock", "Pop"]))
        i.add(TXXX(encoding=1, desc="ARTISTS", text=["A", "B"]))
        i.save(str(p), v2_version=3, v23_sep=None)
        before = tagio.read(p)
        tagio.write(p, {"title": ["Changed"]})
        after = tagio.read(p)
        self.assertEqual({k: v for k, v in after.items() if k != "title"},
                         {k: v for k, v in before.items() if k != "title"})


if __name__ == "__main__":
    unittest.main()
