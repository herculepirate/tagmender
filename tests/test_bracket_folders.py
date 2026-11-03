"""Folder and file names with square brackets ("2016 - Reborn [2CD]", "[2024 RM + RMX 2CD]",
"[RM Restored]") are found as they are: a bracket in a glob pattern is a character class,
so a name used in a pattern must be escaped.

Run:  python3 tests/test_bracket_folders.py   (offline, no audio needed)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm-state-")
os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/Picard.ini"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import executor  # noqa: E402
import match_albums as ma  # noqa: E402

LOG = """Exact Audio Copy V1.0 beta 4

Artist / Album

TOC of the extracted CD

     Track |   Start  |  Length  | Start sector | End sector
    ---------------------------------------------------------
        1  |  0:00.00 |  1:00.00 |         0    |     4499
        2  |  1:00.00 |  1:00.00 |      4500    |     8999
        3  |  2:00.00 |  1:00.00 |      9000    |    13499
"""


class BracketFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-brackets-"))

    def album(self, name):
        d = self.tmp / "Artist" / name
        (d / "Disc 1").mkdir(parents=True)
        (d / "Disc 1" / "rip [EAC].log").write_text(LOG, encoding="utf-8")
        (d / "Scans [hi-res]").mkdir()
        (d / "Scans [hi-res]" / "back.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
        (d / "notes [read me].txt").write_text("Cat#: ABC 123-2\n", encoding="utf-8")
        return d

    def test_logs_scans_and_notes_are_found(self):
        for name in ("2016 - Reborn [2CD]", "2024 - Album [2024 RM + RMX 2CD]", "1990 - Album [RM Restored]",
                     "Album [a-z]*?"):
            d = self.album(name)
            logs = ma.rip_logs(d)
            self.assertEqual(len(logs), 1, name)
            self.assertEqual(logs[0]["toc"].split()[:3], ["1", "3", "13650"], name)
            self.assertEqual([Path(p).name for p in ma.list_images(d, 15)[0]], ["back.jpg"], name)
            self.assertIn("notes [read me].txt", [p.name for p in ma.album_documents(d)], name)

    def test_staged_copies_of_a_bracketed_file_are_found(self):
        d = self.tmp / "2016 - Reborn [2CD]"
        d.mkdir()
        dst = d / "01 Song [Live].flac"
        old = d / f".01 Song [Live].flac{executor.STAGE_TAG}job1.part"
        old.write_bytes(b"x")
        self.assertEqual(executor._old_stage_names(dst, "job1"), [old])


if __name__ == "__main__":
    unittest.main(verbosity=2)
