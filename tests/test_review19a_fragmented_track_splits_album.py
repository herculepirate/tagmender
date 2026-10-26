# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). New behaviour of today's version; the previous version
# fails this test too, differently (it moved the album whole but wrote tags into the fragmented
# file, breaking its audio - review 18).
# plan.Planner.album now drops a fragmented MP4 from the album (skipped, "unsupported_format")
# before anything else is decided - also when the album is to be MOVED (or copied), where the
# file need not be written at all. The rest of the album is moved without it, and because the
# old folder "holds other music too", none of the album's companion files go along:
#  - the album is split in two folders: its other tracks in the new folder, the fragmented
#    track left behind with the album's cover.jpg, its cue sheet (now naming a file that is
#    gone) and even the .lrc lyrics of the tracks that moved;
#  - the plan says "READY" (the preview's summary "1 ready"; the GUI shows the album as ready,
#    the watcher records it "done"); the skip is one line among the album's details.
# Never "an album moves back whole or not at all" - it is half-moved by design.
# Fix: in a move/copy, take the fragmented file along as it is (op "move" with no changes, as a
# file whose tags are already right; no embedding into it) - or block the whole album
# ("a fragmented MP4: ... move it by hand") instead of moving part of it.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, decoded, encode, executor, pl, target  # noqa: E402

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class FragmentedTrackSplitsAlbum(Base):
    def test_move(self):
        d = self.lib / "Some Artist" / "2001 - Frag"
        f1 = encode(d / "01 a.m4a", 440, "-c:a", "aac")
        f2 = encode(d / "02 b.m4a", 550, *FRAGS, duration=3)
        (d / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100 + b"\xff\xd9")
        (d / "01 a.lrc").write_text("[00:00.50]one")
        (d / "rip.cue").write_text('FILE "01 a.m4a" WAVE\n  TRACK 01 AUDIO\nFILE "02 b.m4a" WAVE\n  TRACK 02 AUDIO\n')
        prop = self.proposal(source_dir=str(d), files=[str(f1), str(f2)],
                             targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop], self.profile(operation="move", destination=str(self.dest)))
        executor.Executor(p).run()
        where = {}
        for root in (self.lib, self.dest):
            for dp, _dn, names in os.walk(root):
                for n in names:
                    if n.endswith((".m4a", ".jpg", ".cue", ".lrc")):
                        where.setdefault(dp, []).append(n)
        album_dirs = [dp for dp, names in where.items() if any(n.endswith(".m4a") for n in names)]
        for dp in album_dirs:                               # (every track still plays)
            for n in where[dp]:
                if n.endswith(".m4a"):
                    self.assertNotIn(" ERR ", decoded(Path(dp) / n), f"{n} no longer plays")
        self.assertEqual(len(album_dirs), 1, f"the album was split: {where}")
        self.assertEqual(len(where[album_dirs[0]]), 5, f"the album's files are not together: {where}")


if __name__ == "__main__":
    unittest.main()
