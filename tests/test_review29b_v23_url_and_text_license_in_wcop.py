# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression: last
# round's fix covers several license URLs, not a URL beside a license that is not one).
"""ID3v2.3: two licenses, a URL and a text ("https://a.example/l" and "Some licence"), are
written as one WCOP link frame holding both joined - "https://a.example/l/Some licence", a URL
that leads nowhere - where Picard 3.0.2 writes TXXX:LICENSE (several values: never WCOP).

Checked end to end with Picard's own code (write_id3v23 on, the tagger script
$setmulti(license,https://a.example/l; Some licence)):
  Picard:    TXXX:LICENSE = "https://a.example/l/Some licence"   (no WCOP)
  TagMender: WCOP url="https://a.example/l/Some licence"          (no TXXX:LICENSE)
ID3v2.4 is right (TXXX:LICENSE with both values). With id3v23_join_with "; " the same.

Cause: tagio.write (tagio.py:1078-1083) recognises "several licenses" in a v2.3 tag (whose
values stored() has already joined into one) only by the regex join + "scheme://" - a second
URL. A second value without a scheme is not seen, so the joined text goes on to _set_id3 as
one license, which looks like a valid URL (its start is) and becomes WCOP.

Fix: decide on the values before they are joined (the target's list: more than one value ->
TXXX:LICENSE), not on the joined text.
"""
import sys
import unittest
from pathlib import Path

from mutagen.id3 import ID3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402

SCRIPT = "$setmulti(license,https://a.example/l; Some licence)"


class V23UrlAndTextLicense(unittest.TestCase):
    def _go(self, join):
        files = c.album(".mp3", 2)
        p, res = c.run(files, scripts=[SCRIPT], picard={"write_id3v23": True, "id3v23_join_with": join})
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = ID3(str(files[0]))
        self.assertEqual(t.version[:2], (2, 3))
        self.assertEqual([str(x.url) for x in t.getall("WCOP")], [], "Picard writes no WCOP for two licenses")
        self.assertEqual([list(x.text) for x in t.getall("TXXX:LICENSE")], [[f"https://a.example/l{join}Some licence"]])

    def test_slash(self):
        self._go("/")

    def test_semicolon(self):
        self._go("; ")


if __name__ == "__main__":
    unittest.main()
