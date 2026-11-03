"""Sixteenth review (B), around its fixes: tagger scripts run on each file's own tags (as
Picard's Track.update_file_metadata), and a copy's own edits stay the copy's."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402
import executor  # noqa: E402
import tagio  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
SCRIPTS = {"enable_tagger_scripts": True,
           "list_of_scripts": ["$if(%genre%,,$set(genre,Unknown))$set(comment,%_extension% %_format%)"]}


class PerFile(Base):
    def test_scripts_read_the_files_tags_and_variables(self):
        d = self.album("Band", "Album", genre="Rock")
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), picard=SCRIPTS)
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        ch = a["files"][0]["changes"]
        self.assertNotIn("genre", ch)
        self.assertEqual(ch.get("comment"), ["flac FLAC"])
        self.assertFalse([k for k in ch if k.startswith("~") or k.startswith("_")], ch)

    def test_a_copys_own_edit_stays(self):
        d = self.album("Band", "Album", genre="Rock")
        dest = self.tmp / "dest"
        dest.mkdir()
        kw = dict(operation="copy", destination=str(dest), sources=[{"path": str(self.lib)}], picard=SCRIPTS)
        http = FakeHttp(mb=[mb_release(MBID, "Band", "Album")])
        e = self.engine(http, **kw)
        e.force = {os.path.realpath(d): MBID}
        p = e.plan(d)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        copy = sorted(dest.rglob("*.flac"))[0]
        tagio.write(copy, {"genre": ["Jazz"]})                  # the copy edited by hand
        e = self.engine(http, **kw)
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        mine = next(fe for fe in a["files"] if fe["dst"] == str(copy))
        self.assertNotIn("genre", mine["changes"], mine["diff"])


if __name__ == "__main__":
    unittest.main()
