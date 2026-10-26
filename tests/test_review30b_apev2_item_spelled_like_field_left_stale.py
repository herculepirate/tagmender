# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too).
"""WavPack / Monkey's Audio / Musepack: an APEv2 item that carries one of Picard's fields under
Picard's own (lower-cased) field name rather than Picard's APEv2 key - RELEASETYPE (ffmpeg
writes it when converting a Picard-tagged FLAC), ALBUMARTIST (Mp3tag), TRACKNUMBER/DISCNUMBER -
is left with its OLD value while TagMender writes the new value under Picard's key beside it.

Picard 3.0.2 reads APEv2 keys case-insensitively as its field names and keeps the file's
spelling (apev2.py _load: `self.__casemap[name] = origname`; _get_tag_name returns it), so it
writes the new value INTO that item:

  file RELEASETYPE "album" (release: single)
      Picard: RELEASETYPE "single"
      TagMender: RELEASETYPE "album" kept + MUSICBRAINZ_ALBUMTYPE "single"
  file ALBUMARTIST "Old AA"
      Picard: ALBUMARTIST "Adele"
      TagMender: ALBUMARTIST "Old AA" kept + "Album Artist" "Adele"
  file TRACKNUMBER "1", DISCNUMBER "1"
      Picard: TRACKNUMBER "1/2", DISCNUMBER "1/1"
      TagMender: TRACKNUMBER "1", DISCNUMBER "1" kept + Track "1/2", Disc "1/1"

So the files end up with two conflicting values for the release type / album artist / numbers,
and players reading the other key show the stale one. Checked end to end with Picard's own
code on real ffmpeg conversions and hand-made items (r30B_work/j_ffconv.py, j_ape.py).

Cause: tagio.read's APEv2 branch (~533-551) reads only Picard's keys (_APE_R) as the fields; any
other spelling is a user tag ("~custom:" when the field is also present), and tagio.write
always writes Picard's canonical key (APE map, ~122) instead of the item the file already uses.

Fix: for APEv2, read an item whose lower-cased key is a Picard field name as that field and
write the field back into the item spelling the file already has (Picard's casemap), so no
stale duplicate is left.
"""
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def items(files, **kv):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        for k, v in kv.items():
            m.tags[k] = v
        m.save()


def value(path, key):
    t = mutagen.File(str(path)).tags
    return str(t[key]) if key in t else None


class ApeItemsStale(unittest.TestCase):
    def test_releasetype_item(self):
        files = c.album(".wv", 2)
        items(files, RELEASETYPE="album")
        targets = c.proposal(files).targets
        for t in targets:
            t["releasetype"] = ["single"]
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(value(files[0], "RELEASETYPE"), "single", "Picard writes the release type there")

    def test_albumartist_item(self):
        files = c.album(".wv", 2)
        items(files, ALBUMARTIST="Old AA")
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(value(files[0], "ALBUMARTIST"), "Pink Floyd", "Picard writes the album artist there")

    def test_number_items(self):
        files = c.album(".wv", 2)
        items(files, TRACKNUMBER="1", DISCNUMBER="1")
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual((value(files[0], "TRACKNUMBER"), value(files[0], "DISCNUMBER")), ("1/2", "1/1"))


if __name__ == "__main__":
    unittest.main()
