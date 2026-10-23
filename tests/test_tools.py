# Tests for dupes_fp.py and health_report.py.
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import dupes_fp  # noqa: E402
import health_report  # noqa: E402


def make(p, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    *meta, str(p), "-y"], check=True)


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_recording_in_two_libraries(self):
        rid = "11111111-2222-3333-4444-555555555555"
        make(self.tmp / "ambient" / "A" / "2001 - Album" / "01.flac", MUSICBRAINZ_TRACKID=rid)
        make(self.tmp / "ambient" / "A" / "2001 - Album" / "02.flac")
        make(self.tmp / "extreme" / "A" / "2002 - Single" / "01.mp3", MUSICBRAINZ_TRACKID=rid)
        out = self.tmp / "d.csv"
        dupes_fp.main([str(self.tmp / "ambient"), str(self.tmp / "extreme"), "--out", str(out)])
        rows = out.read_text().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn("yes", rows[1])
        self.assertIn("2001 - Album", rows[1])                     # the lossless album copy is kept

    def test_health_page(self):
        audit = self.tmp / "audit.csv"
        audit.write_text("Status,FullPath\ntagged_both,a\nuntagged,b\nmusicbrainz_only,c\n")
        out = self.tmp / "h.html"
        health_report.main(["--library", "extreme", str(audit), str(self.tmp / "none"),
                            "--tagging-dir", str(self.tmp), "--out", str(out)])
        page = out.read_text()
        self.assertIn("67% <small>tagged (2 of 3 albums)", page)
        self.assertIn("prefers-color-scheme:dark", page)


if __name__ == "__main__":
    unittest.main()
