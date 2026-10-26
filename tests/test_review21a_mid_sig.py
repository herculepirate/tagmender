# Twenty-first independent review (A): a fingerprint made by 609b541 to b1f0b98 (which read
# described comments by their description, and split Vorbis "text (desc)" comments) matches.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21a_common  # noqa: E402,F401
from _review13b_common import make  # noqa: E402

import executor  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402
from mutagen.id3 import COMM, ID3  # noqa: E402


class MidSignatures(unittest.TestCase):
    def test_609b541_fingerprints(self):
        d = Path(tempfile.mkdtemp())
        m = make(d / "a.mp3")
        i = ID3(str(m))
        i.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=[" 0000"]))
        i.save(str(m))
        fl = make(d / "b.flac")
        x = FLAC(str(fl))
        x["COMMENT"] = ["Exact Audio Copy (ripper)"]
        x.save()
        for p in (m, fl):
            mid = executor._tags_sig(p, legacy=2)
            self.assertNotEqual(mid, executor._tags_sig(p, legacy=1))
            self.assertTrue(executor._sig_is(p, mid), p)


if __name__ == "__main__":
    unittest.main()
