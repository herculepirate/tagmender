"""Opus: a cover (or artist photo) TagMender embeds now carries width and height but colour
depth 0 - a picture block libopusfile rejects, so players built on it show no cover.

Last round's fix (executor.py:792 embed_front, :724 embed_picture - their Ogg branch) sets
`pic.width, pic.height = _image_size(data)` for Ogg Vorbis, Ogg FLAC *and Opus*, leaving
depth (and colours) 0. libopusfile's opus_picture_tag_parse() refuses a picture where some of
width/height/depth are set and others are 0 ("If one of these is set, they all must be";
OP_ENOTFORMAT). Picard 3.0.2 sets width/height for Vorbis and FLAC but deliberately not for
Opus (picard/formats/vorbis.py:361-366, PICARD-2909: "libopus expects width, height and depth
to be either all zero or all non-zero"). Before last round TagMender wrote all zeros, as
Picard does.

Regression: passes with TM_REPO=prev.
"""
import base64
import io
import unittest

import _review28b_common as c
import executor
import mutagen
from mutagen.flac import Picture


def jpeg(w=300, h=200):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def pictures(path):
    f = mutagen.File(str(path))
    return [Picture(base64.b64decode(v)) for v in f.tags.get("METADATA_BLOCK_PICTURE", [])]


class OpusPictureDimensions(unittest.TestCase):
    def check(self, pic):
        dims = (pic.width, pic.height, pic.depth)
        self.assertTrue(all(dims) or not any(dims),
                        f"width/height/depth {dims}: libopusfile accepts all set or all 0 (Picard: all 0)")
        self.assertEqual((pic.width, pic.height), (0, 0), "Picard 3.0.2 leaves Opus picture dimensions 0")

    def test_front(self):
        f = c.album(".opus", 1)[0]
        executor.embed_front(f, jpeg())
        pics = pictures(f)
        self.assertEqual(len(pics), 1)
        self.check(pics[0])

    def test_artist_photo(self):
        f = c.album(".opus", 1)[0]
        executor.embed_picture(f, jpeg())
        pics = pictures(f)
        self.assertEqual(len(pics), 1)
        self.check(pics[0])


if __name__ == "__main__":
    unittest.main()
