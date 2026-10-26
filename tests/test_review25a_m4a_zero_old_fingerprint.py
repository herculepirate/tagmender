# Twenty-fifth independent review (A): an M4A whose track or disc number is 0 ("0/0", common)
# is now read as "0", as Picard reads it; a job's "v2:" fingerprint recorded by the version
# before read it as none. Undo of such a job must still take the file as unedited.
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="tm25A-config-"))
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp(prefix="tm25A-state-"))
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import executor  # noqa: E402
import tagio  # noqa: E402


class OldFingerprintTrackZero(unittest.TestCase):
    def test_v2_fingerprint_read_without_zero_matches(self):
        from mutagen.mp4 import MP4
        d = Path(tempfile.mkdtemp())
        f = d / "01 a.m4a"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "aac", "-y", str(f)], check=True)
        m = MP4(str(f))
        m["\xa9nam"] = ["a"]
        m["disk"] = [(0, 0)]
        m.save()
        self.assertEqual(tagio.read(f).get("discnumber"), ["0"])            # (as Picard reads it)
        old = executor._tags_sig(f, mp4_zero=False)                        # (the version before)
        self.assertNotEqual(old, executor._tags_sig(f))
        self.assertTrue(executor._sig_is(f, old))
        m["\xa9nam"] = ["edited"]
        m.save()
        self.assertFalse(executor._sig_is(f, old))                         # (an edit is still one)


if __name__ == "__main__":
    unittest.main()
