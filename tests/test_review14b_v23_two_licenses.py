# Fourteenth independent review (Picard code): this test failed before its fix.
"""tagio.stored() + write() on an ID3v2.3 tag: two license URLs are first joined into one
value ("http://a.com/x/http://b.com/y"), which then passes write()'s "one URL" test, so a
WCOP frame is written holding that made-up URL. Picard 3.0.2 (id3.py _save_standard_tag:
WCOP only for exactly one license URL) writes TXXX:LICENSE "http://a.com/x/http://b.com/y"
and no WCOP (checked by running Picard's MP3File._save with write_id3v23)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tagio  # noqa: E402


class TwoLicenses(unittest.TestCase):
    def test_no_wcop_for_two_urls(self):
        from mutagen.id3 import ID3
        p = make(Path(tempfile.mkdtemp()) / "a.mp3")
        ID3().save(str(p), v2_version=3)
        target = {"title": ["T"], "license": ["http://a.com/x", "http://b.com/y"]}
        changes = tagio.stored(p, target) if hasattr(tagio, "stored") else target
        tagio.write(p, changes)
        raw = ID3(str(p), translate=False)
        self.assertEqual(raw.getall("WCOP"), [], f"a WCOP frame with a made-up URL: {raw.getall('WCOP')}")
        self.assertEqual([str(x) for fr in raw.getall("TXXX:LICENSE") for x in fr.text],
                         ["http://a.com/x/http://b.com/y"])


if __name__ == "__main__":
    unittest.main()
