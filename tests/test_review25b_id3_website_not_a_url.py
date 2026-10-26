# Twenty-fifth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A tagger script that sets "website" to something that is not a full URL
($set(website,www.example.com), $set(website,mailto:me@example.com), a value built from
%_dirname% ...) gets a TXXX:website frame in an MP3 (or WAV/AIFF) - Picard writes nothing
for it.

Cause: tagio._set_id3 (tagio.py:1148-1158): for "website" it writes WOAR frames when every
value is a URL, and otherwise falls through to the free-text branch, which adds
TXXX:website. Picard's ID3File._save_standard_tag (picard/formats/id3.py:853-866) writes
WOAR only when every value has a scheme and a host (urlparse(v)[:2]) and otherwise writes
no frame at all (only "license" falls back to TXXX:LICENSE).

Picard 3.0.2 end to end (tagger script $set(website,www.example.com), MP3): no WOAR, no
TXXX:website. TagMender: TXXX(desc='website', text=['www.example.com'])."""
import sys
import unittest
from pathlib import Path

from mutagen.id3 import ID3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402


class WebsiteNotAUrl(unittest.TestCase):
    def check(self, value):
        files = c.album(".mp3", 1)
        _p, res = c.run(files, ["$set(website,%s)" % value])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        tags = ID3(str(files[0]))
        frames = [k for k in tags.keys() if k.upper().startswith(("TXXX:WEBSITE", "WOAR"))]
        self.assertEqual(frames, [], f"Picard writes no frame for website={value!r}")

    def test_no_scheme(self):
        self.check("www.example.com")

    def test_no_host(self):
        self.check("mailto:me@example.com")


if __name__ == "__main__":
    unittest.main()
