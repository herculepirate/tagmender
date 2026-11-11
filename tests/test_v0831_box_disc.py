# BOX-2 (docs/MATCHING_RULES.md): one disc of a set in a folder of its own (Allan Holdsworth -
# Against the Clock: "Volume One Guitar" and "Volume Two Synthaxe" as two folders) is matched to its
# disc of the release, and tagged from THAT disc's tracks (disc 1 of 2) - before, the release's
# whole tracklist did not line up with the folder's files and the album was left Unresolved.
# Synthetic audio (ffmpeg) and a fake Discogs; no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, dg_release  # noqa: E402


def two_disc_release(rid):
    rel = dg_release(rid, "Band S", "The Set")
    rel["formats"] = [{"name": "CD", "qty": "2", "descriptions": ["Compilation"]}]
    rel["tracklist"] = (
        [{"type_": "heading", "position": "", "title": "Volume One", "duration": ""}] +
        [{"type_": "track", "position": f"1-{i + 1}", "title": f"T{i}", "duration": d}
         for i, d in enumerate(("0:10", "0:20", "0:30"))] +
        [{"type_": "heading", "position": "", "title": "Volume Two", "duration": ""}] +
        [{"type_": "track", "position": f"2-{i + 1}", "title": f"U{i}", "duration": d}
         for i, d in enumerate(("0:45", "0:55", "1:05"))])
    return rel


class BOX2(Base):
    def test_BOX2_one_disc_of_a_set_is_tagged_from_its_own_tracks(self):
        d = self.album("Band S", "The Set. Volume One", folder="2005 - The Set. Volume One")
        eng = self.engine(FakeHttp(dg=[two_disc_release(4401)]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified", (p.reason, p.why, p.evidence))
        self.assertEqual([t.get("discnumber") for t in p.targets], [["1"]] * 3)
        self.assertEqual([t.get("title") for t in p.targets], [["T0"], ["T1"], ["T2"]])
        self.assertEqual(p.targets[0].get("totaldiscs"), ["2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
