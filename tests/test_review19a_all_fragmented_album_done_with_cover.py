# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). New behaviour of today's version (the previous version
# tagged the files and broke their audio - review 18 - so it fails this test too).
# An album whose every track is a fragmented MP4 (a streaming download): plan.Planner.album
# skips each file in its per-file loop ("unsupported_format"), but - unlike the files a profile
# filter drops before ("if not kept: status skipped") - nothing then says the album is skipped:
#  - the album stays READY ("1 ready" in the preview; the GUI lists it ready), the job reports it
#    "done", and the watcher records it done (never looked at again until its files change);
#  - the rest of the plan still runs for it: the release's cover is saved as cover.jpg into the
#    folder (the artist photo, lyrics files or a move of its companion files likewise, as
#    planned), for an album TagMender did not tag at all.
# Fix: after the loop, an album with no file left (every one skipped) gets status "skipped"
# with the reason (like the profile filters), and no artwork/companions/sidecars are planned.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, encode, executor, pl, target  # noqa: E402

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class AllFragmentedAlbumDoneWithCover(Base):
    def test_in_place(self):
        from PIL import Image
        d = self.lib / "Some Artist" / "2001 - Frag"
        fs = [encode(d / f"0{n} t.m4a", 440 + 100 * n, *FRAGS, duration=3) for n in (1, 2)]
        img = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (200, 10, 10)).save(img)
        prop = self.proposal(source_dir=str(d), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")],
                             artwork={"front": str(img), "source": "test", "width": 600, "height": 600})
        p = pl.build([prop], self.profile())
        a = p["albums"][0]
        orig = [f.read_bytes() for f in fs]
        executor.Executor(p).run()
        # (after the review's fix: fragmented files are never written to - they go with their
        # album as they are, said in the plan; the album's cover file beside them is no write
        # into them)
        self.assertEqual([f.read_bytes() for f in fs], orig)
        self.assertTrue(all(fe.get("as_is") and not fe["changes"] for fe in a["files"]), a["files"])
        self.assertTrue(any("fragmented MP4" in n for n in a["notes"]), a["notes"])
        self.assertFalse((a.get("artwork") or {}).get("embed_into"))


if __name__ == "__main__":
    unittest.main()
