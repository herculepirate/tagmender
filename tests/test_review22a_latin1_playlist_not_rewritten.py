# Twenty-second independent review (A). Not a regression: the previous version does the same.
# An older rip on a Linux disk: the track names are Latin-1 bytes ("01 H\xe9lls Bells.mp3", not
# valid UTF-8 - Python sees "01 H\udce9lls Bells.mp3"), and so is the playlist beside them
# (cp1252/Latin-1 text naming those files - exactly the bytes on disk, so players find them).
# A move profile renames the tracks and takes the playlist along. plan._cue_rewrites decodes
# the playlist as cp1252 ("01 Hélls Bells.mp3") but looks its lines up in a table keyed by the
# surrogate-escaped names ("01 H\udce9lls Bells.mp3"): nothing matches, the preview says
# nothing about the playlist's entries, and it is moved with its old names - every entry now
# points at a file that no longer exists (the cue sheet of such a rip the same).
# Fix: key the table by the names' bytes decoded the way the sheet is decoded (os.fsencode(name)
# .decode(enc)), and write new names back in that encoding (or note that they cannot be).
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review22a_common import Base, encode, executor, pl, target  # noqa: E402


class Latin1Playlist(Base):
    def test_moved_playlist_names_the_moved_tracks(self):
        d = self.lib / "AC-DC" / "1980 - Back in Black"
        fs = [encode(d / os.fsdecode(b"0%d H\xe9lls Bells %d.mp3" % (n, n)), 500 + n) for n in (1, 2)]
        m3u = d / "Back in Black.m3u"
        m3u.write_bytes(b"01 H\xe9lls Bells 1.mp3\r\n02 H\xe9lls Bells 2.mp3\r\n")   # (the bytes on disk)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(d), files=[str(f) for f in fs],
                             targets=[target(1, "Hells Bells"), target(2, "Shoot to Thrill")])
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = next(self.dest.rglob("*.m3u"))
        names = [os.fsdecode(x) for x in new.read_bytes().splitlines() if x.strip()]
        missing = [n for n in names if not (new.parent / n).exists()]
        self.assertEqual(missing, [], f"the moved playlist names files that are not there: "
                                      f"{names}; folder has {sorted(x.name for x in new.parent.iterdir())}")


if __name__ == "__main__":
    unittest.main()
