# Fourteenth independent review (Picard code): this test failed before its fix.
"""tagio.write(): a performer credit an earlier TagMender stored as a custom tag (ID3
TXXX:performer:<role>, Vorbis PERFORMER:<ROLE> - read as "~legacy:performer:<role>") is
dropped when the credit is written properly. Since the role keeps its case
(field_name(): "performer:Hammond organ"), the old tag is matched with
`x[5:].lower() == k` / `x.lower() == k` against the mixed-case key and never matches: the
legacy tag stays, so Picard (and every player) shows the credit twice."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tagio  # noqa: E402


class LegacyCreditGoes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_id3_legacy_txxx_removed_for_capitalised_role(self):
        from mutagen.id3 import ID3, TXXX
        p = make(self.tmp / "a.mp3")
        i = ID3()
        i.add(TXXX(encoding=3, desc="performer:hammond organ", text=["Jon Lord"]))   # an older TagMender's
        i.save(str(p))
        self.assertIn("~legacy:performer:hammond organ", tagio.read(p))
        tagio.write(p, {"performer:Hammond organ": ["Jon Lord"]})
        got = tagio.read(p)
        self.assertNotIn("~legacy:performer:hammond organ", got,
                         f"the old custom credit stayed beside the new one: {got}")

    def test_vorbis_legacy_key_removed_for_capitalised_role(self):
        from mutagen.flac import FLAC
        p = make(self.tmp / "a.flac")
        f = FLAC(str(p))
        f["PERFORMER:HAMMOND ORGAN"] = ["Jon Lord"]                                  # an older TagMender's
        f.save()
        self.assertIn("~legacy:performer:hammond organ", tagio.read(p))
        tagio.write(p, {"performer:Hammond organ": ["Jon Lord"]})
        got = tagio.read(p)
        self.assertNotIn("~legacy:performer:hammond organ", got,
                         f"the old custom credit stayed beside the new one: {got}")


if __name__ == "__main__":
    unittest.main()
