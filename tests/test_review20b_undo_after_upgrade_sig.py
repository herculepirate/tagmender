# Twentieth independent review (B): a job's tag fingerprint made by an earlier version (which
# read no described comments, nor split "text (desc)" comments) still matches its file.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401
from _review13b_common import make  # noqa: E402

import executor  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402
from mutagen.id3 import COMM, ID3  # noqa: E402


class OldSignatures(unittest.TestCase):
    def test_old_and_new_fingerprints(self):
        d = Path(tempfile.mkdtemp())
        m = make(d / "a.mp3")
        i = ID3(str(m))
        i.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=[" 0000"]))
        i.add(COMM(encoding=3, lang="eng", desc="", text=["plain"]))
        i.save(str(m))
        fl = make(d / "b.flac")
        x = FLAC(str(fl))
        x["COMMENT"] = ["Recorded live (2001)"]
        x.save()
        for p in (m, fl):
            old = executor._tags_sig(p, legacy=True)
            new = executor._tags_sig(p)
            self.assertNotEqual(old, new.split(":", 1)[1])          # (the two readings differ here)
            self.assertTrue(executor._sig_is(p, old), p)
            self.assertTrue(executor._sig_is(p, new), p)
            self.assertFalse(executor._sig_is(p, "0" * 40))


if __name__ == "__main__":
    unittest.main()
