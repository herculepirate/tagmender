# Thirtieth independent review (B). Not a regression (payload.py is unchanged; fails with
# TM_REPO=prev too).
"""A WavPack (or Monkey's Audio, Musepack, an MP3 with an APEv2 trailer) whose APEv2 tag is
exactly 131 bytes long cannot be tagged: the album fails with

    "track01.wv: audio data differed after writing tags; original kept; album put back as it was"

on every run - a scary message about audio corruption that is not true.

Cause: payload._trailer_start (payload.py ~45-77) looks for an ID3v1 tag first, by "TAG" at
128 bytes from the end. An APEv2 tag of 131 bytes (32-byte header + 67 bytes of items + 32-byte
footer) has its header "APETAGEX" starting 131 bytes from the end, so its letters "TAG" sit
exactly 128 bytes from the end: the APEv2 tag is taken for an ID3v1 tag, 128 bytes are cut
off and 3 bytes of the APEv2 header are hashed as audio. After the write the tag has another
size, the hash covers different bytes, and the "audio changed" check fails. (A tag whose text
contains "TAG" 128 bytes from the end - "TAGGED", "VINTAGE" - trips it the same way.)

Reproduced with the file a converter makes: ffmpeg's "encoder" item plus Track "5/9" and
TOTALTRACKS "9" is exactly 131 bytes with this machine's ffmpeg; here built directly.

Fix: in _trailer_start, accept "TAG" at end-128 as ID3v1 only when it is not part of an APEv2
header/footer (e.g. the 8 bytes at end-131 are not b"APETAGEX"), or check for the APEv2 footer
first; better, parse from the end the way mutagen does (ID3v1 only if the APEv2 footer check
fails at end-32 - and, after stripping ID3v1, APEv2 at end-128-32).
"""
import sys
import unittest
from pathlib import Path

from mutagen.apev2 import APEv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402
import payload  # noqa: E402


def ape_131(path):
    APEv2(str(path)).delete(str(path)) if _has_ape(path) else None
    t = APEv2()
    t["Comment"] = "c" * 51          # item: 8 + len("Comment\0") + 51 = 67 bytes; tag 32 + 67 + 32 = 131
    t.save(str(path))
    data = open(path, "rb").read()
    assert data[-131:-123] == b"APETAGEX" and data[-128:-125] == b"TAG", "a 131-byte APEv2 tag"


def _has_ape(path):
    try:
        APEv2(str(path))
        return True
    except Exception:
        return False


class Ape131(unittest.TestCase):
    def test_digest_ignores_the_tag(self):
        f = c.album(".wv", 1)[0]
        APEv2(str(f)).delete(str(f))
        bare = payload.digest(f)
        ape_131(f)
        self.assertEqual(payload.digest(f), bare, "the audio digest must not depend on the APEv2 tag")

    def test_album_is_tagged(self):
        files = c.album(".wv", 2)
        for f in files:
            ape_131(f)
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))


if __name__ == "__main__":
    unittest.main()
