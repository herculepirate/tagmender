"""The front cover TagMender embeds into FLAC and Ogg Vorbis files has width and height 0 in
its PICTURE block.

Picard 3.0.2 (formats/vorbis.py VCommentFile._save) fills picture.width and picture.height
from the image for FLAC and Ogg Vorbis (only Opus is left at 0, PICARD-2909); TagMender's own
CoverUp batch path does too (coverup_batch.embed_front: width, height, depth 24). The planner's
path - executor.embed_front, which `tagmender run` uses for every album that needs a cover -
leaves them 0: `metaflac --list` shows "width: 0, height: 0", and software that takes the
size from the block header rather than decoding the image sees a 0x0 picture. Fails with
TM_REPO=prev too.
"""
import base64
import subprocess
import tempfile
import unittest
from pathlib import Path

import mutagen
from mutagen.flac import Picture

import _review27b_common as c
import executor


def jpeg(w=600, h=400):
    out = Path(tempfile.mkdtemp(prefix="tm27B-img-")) / "front.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=red:s={w}x{h}", "-frames:v", "1",
                    "-y", str(out)], check=True)
    return out.read_bytes()


class PictureDimensions(unittest.TestCase):
    def test_flac(self):
        f = c.album(".flac", 1)[0]
        executor.embed_front(str(f), jpeg(), "image/jpeg")
        pic = mutagen.File(str(f)).pictures[0]
        self.assertEqual((pic.type, pic.width, pic.height), (3, 600, 400))

    def test_ogg_vorbis(self):
        f = c.album(".ogg", 1)[0]
        executor.embed_front(str(f), jpeg(), "image/jpeg")
        pic = Picture(base64.b64decode(mutagen.File(str(f)).tags["METADATA_BLOCK_PICTURE"][0]))
        self.assertEqual((pic.type, pic.width, pic.height), (3, 600, 400))


if __name__ == "__main__":
    unittest.main()
