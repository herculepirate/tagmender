# Thirteenth independent review (Picard code): this test failed before its fix.
"""WAV: Picard 3.0.2 writes the RIFF LIST INFO chunk too (write_wave_riff_info, on by default;
picard/formats/wav.py: INAM title, IART artist, IPRD album, ICRD date, ITRK track ...).

tagio writes only the ID3 chunk, so a WAV whose ripper left INFO tags keeps the old title,
artist and album there - what Windows Explorer, Audacity and many players show for WAV."""
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review13b_common as _common  # noqa: E402,F401

import tagio  # noqa: E402


def riff_info(path):
    data = Path(path).read_bytes()
    out, i = {}, 12
    while i + 8 <= len(data):
        cid, size = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        if cid == b"LIST" and data[i + 8:i + 12] == b"INFO":
            j, end = i + 12, i + 8 + size
            while j + 8 <= end:
                sid, ssize = data[j:j + 4].decode("latin-1"), struct.unpack("<I", data[j + 4:j + 8])[0]
                out[sid] = data[j + 8:j + 8 + ssize].rstrip(b"\0").decode("latin-1")
                j += 8 + ssize + (ssize & 1)
        i += 8 + size + (size & 1)
    return out


class WavInfo(unittest.TestCase):
    def test_info_follows_the_new_tags(self):
        p = Path(tempfile.mkdtemp()) / "t.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "pcm_s16le",
                        "-metadata", "title=Track 01", "-metadata", "artist=Unknown Artist",
                        "-metadata", "album=Unknown Album", "-y", str(p)], check=True)
        self.assertEqual(riff_info(p).get("INAM"), "Track 01")
        tagio.write(p, {"title": ["Real Title"], "artist": ["Real Artist"], "album": ["Real Album"]})
        info = riff_info(p)
        self.assertEqual((info.get("INAM"), info.get("IART"), info.get("IPRD")),
                         ("Real Title", "Real Artist", "Real Album"))


if __name__ == "__main__":
    unittest.main()
