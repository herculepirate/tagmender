"""ID3 frames Picard always writes in Latin-1 are written in UTF-8 (UTF-16 in v2.3): the
number frames TRCK / TPOS / MVIN and the cover's APIC.

tagio.py:1099 (_write_pair) adds TRCK/TPOS/MVIN with encoding=3; executor.py:788
(embed_front) and :707 (embed_picture) add APIC with encoding=3. Picard 3.0.2
(picard/formats/id3.py:745-755 _save_number_tag, :776-783 _save_images_to_tags) writes
these with Id3Encoding.LATIN1 whatever its "ID3v2 text encoding" setting, so a number or
cover frame written by TagMender differs byte for byte from Picard's (in ID3v2.3 mutagen
turns UTF-8 into UTF-16 with a BOM, for a frame holding only digits and "/").
docs/PICARD_COVERAGE.md "ID3v2 text encoding | UTF-8 | Partial | always UTF-8" describes the
text-encoding option, not these frames, which Picard never writes in UTF-8. No player-visible
text changes; strict tools and players that only read Latin-1 number/picture frames see the
difference. Fails with TM_REPO=prev too (not a regression).
"""
import unittest
from pathlib import Path

import _review28b_common as c
import executor
from mutagen.id3 import ID3

LATIN1 = 0


class Latin1Frames(unittest.TestCase):
    def test_number_frames_latin1(self):
        files = c.album(".mp3", 2)
        p, res = c.run(files, scripts=["$set(movementnumber,2)$set(movementtotal,3)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        tags = ID3(str(files[0]))
        for fid in ("TRCK", "TPOS", "MVIN"):
            self.assertTrue(tags.getall(fid), fid)
            self.assertEqual(int(tags.getall(fid)[0].encoding), LATIN1,
                             f"Picard writes {fid} in Latin-1 ({tags.getall(fid)[0]!r})")

    def test_apic_latin1(self):
        from PIL import Image
        import io
        f = c.album(".mp3", 1)[0]
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (1, 2, 3)).save(buf, "JPEG")
        executor.embed_front(f, buf.getvalue())
        apic = ID3(str(f)).getall("APIC")[0]
        self.assertEqual(int(apic.encoding), LATIN1, "Picard writes APIC in Latin-1")


if __name__ == "__main__":
    unittest.main()
