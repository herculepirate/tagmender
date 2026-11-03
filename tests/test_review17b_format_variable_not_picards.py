# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B).
"""tagio.file_vars gives %_format% as the format class's NAME (tagio.FORMAT_NAMES, "picard/
formats/*.py NAME"), but several Picard formats override it in _info: an MP3 is
"MPEG-1 Layer 3 - ID3v2.4" (picard/formats/id3.py MP3File._info: 'MPEG-1 Layer %d%s' with
the ID3 version), an M4A "MPEG-4 Audio (AAC LC)" (mp4.py: NAME plus codec_description),
Musepack "Musepack, SV7", OptimFROG "OptimFROG Lossless Audio", an AAC with APEv2 "AAC
(APEv2)". TagMender gives "MPEG-1 Audio" and "MPEG-4 Audio", so a tagger or naming script
testing %_format% ($if($startswith(%_format%,MPEG-1 Layer 3),MP3/) ...) takes another branch
than in Picard. Expected values below are what Picard 3.0.2's own loaders return for these
files (checked with picard-src: test.formats.common.load_metadata(...)['~format'])."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

import tagio  # noqa: E402


class FormatVariableNotPicards(unittest.TestCase):
    def make(self, ext, codec):
        p = Path(tempfile.mkdtemp()) / f"t.{ext}"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", *codec,
                        "-metadata", "title=x", "-y", str(p)], check=True)
        return p

    def test_mp3_format_as_picard(self):
        p = self.make("mp3", ["-c:a", "libmp3lame"])          # (ffmpeg writes an ID3v2.4 tag)
        self.assertEqual(tagio.file_vars(p)["~format"], ["MPEG-1 Layer 3 - ID3v2.4"])

    def test_m4a_format_as_picard(self):
        p = self.make("m4a", ["-c:a", "aac"])
        self.assertEqual(tagio.file_vars(p)["~format"], ["MPEG-4 Audio (AAC LC)"])


if __name__ == "__main__":
    unittest.main()
