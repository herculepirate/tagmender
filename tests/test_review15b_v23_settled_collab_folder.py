# Fifteenth independent review (B): this test failed before its fix.
"""plan.py names a copy/move destination from merged = the file's tags + the changes. A field
that does not change keeps the file's own value - and an ID3v2.3 file holds several values
joined with "/" (TXXX:ALBUMARTISTS "A/B"). The fix of review 14 (raw.get(k) for the CHANGED
fields) covers only the first run: once the tags are written, the next plan has no change for
ALBUMARTISTS, renders %_albumartists_sorted% from the file's "A/B" = "A_B", and moves the
album out of the "A & B" folder its first run put it in. The plan never settles; the same album
in FLAC stays put."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)


class V23SecondRun(Base):
    def run_twice(self, ext, v23):
        album = self.lib / "In" / f"x{ext[1:]}{v23}"
        files = [audio(album / f"0{i} t{ext}", 440 + 100 * i, title=f"t{i}") for i in (1, 2)]
        if v23:
            from mutagen.id3 import ID3
            for f in files:
                ID3(str(f)).save(str(f), v2_version=3)
        tg = [target(i, f"T{i}", artist="A & B", albumartists=["A", "B"]) for i in (1, 2)]
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""})
        p1 = pl.build([self.proposal(source_dir=str(album), files=[str(f) for f in files], targets=tg)], prof)
        res = executor.Executor(p1).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = [Path(fe["dst"]) for fe in p1["albums"][0]["files"]]
        first = sorted({str(m.parent.relative_to(self.dest)) for m in moved})
        # the same release again, for the files where the first run put them
        p2 = pl.build([self.proposal(source_dir=str(moved[0].parent), files=[str(m) for m in moved],
                                     targets=tg)], prof)
        second = sorted({str(Path(fe["dst"]).parent.relative_to(self.dest)) for fe in p2["albums"][0]["files"]})
        ops = [fe["op"] for fe in p2["albums"][0]["files"]]
        return first, second, ops

    def test_flac_settles(self):
        first, second, ops = self.run_twice(".flac", False)
        self.assertEqual(first, ["A & B/2001 - Real Album"])
        self.assertEqual(second, first)
        self.assertEqual(ops, ["none", "none"])

    def test_v23_mp3_settles_where_its_first_run_put_it(self):
        first, second, ops = self.run_twice(".mp3", True)
        self.assertEqual(first, ["A & B/2001 - Real Album"])
        self.assertEqual(second, first, "the second run moves the ID3v2.3 album out of its folder")
        self.assertEqual(ops, ["none", "none"], "the plan never settles")


if __name__ == "__main__":
    unittest.main()
