# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). A regression: passes with TM_REPO=prev.
# _backup_raw (kind 4) now records an MP4 whose ilst is EMPTY - an M4A whose tags were all
# removed by mutagen, Picard, Mp3tag ... (mutagen keeps udta/meta/ilst and the free padding) -
# the same as one with no tags at all: {"tags": None, "mp4_udta": True} ("no tags, or an empty
# list: alike - none"), dropping the room the tag had. Undo then runs f.delete() (mutagen saves
# with padding 0):
#  - the file comes back without its "free" padding (29 bytes smaller here; with the moov in
#    front, every chunk offset is rewritten as well) - not as it was, and not a limit the release
#    notes give; the undo says "put back". The previous version recorded {"mp4": [], "padding": n}
#    and put it back byte for byte.
# Fix: keep the padding for an MP4 that has an (empty) ilst - record {"mp4": [], "padding": n} as
# before and save with that padding - and reserve {"mp4_udta": ...} for f.tags None.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import Base, decoded, encode, executor, pl, target  # noqa: E402


class EmptyIlst(Base):
    def check(self, *args):
        from mutagen.mp4 import MP4
        f = encode(self.lib / "A" / ("m" + "".join(args)) / "01 t.m4a", 440, "-c:a", "aac", *args)
        m = MP4(f)
        m.tags.clear()                                    # ("remove all tags")
        m.save()
        self.assertEqual(len(MP4(f).tags), 0)
        orig, played = f.read_bytes(), decoded(f)
        prop = self.proposal(source_dir=str(f.parent), files=[str(f)], targets=[target(1, "One")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        out = executor.undo(res["job"])
        self.assertEqual(decoded(f), played)
        self.assertEqual(len(f.read_bytes()) - len(orig), 0, f"undo changed its size: {out}")
        self.assertEqual(f.read_bytes(), orig, f"not as it was after undo: {out}")

    def test_moov_last(self):
        self.check()

    def test_faststart(self):
        self.check("-movflags", "faststart")


if __name__ == "__main__":
    unittest.main()
