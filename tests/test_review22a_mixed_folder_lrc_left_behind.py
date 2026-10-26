# Twenty-second independent review (A). Not a regression: the previous version does the same.
# A "Singles" folder (or any folder two releases share) with each track's own time-synced
# lyrics beside it: "Alpha - One.mp3" + "Alpha - One.lrc", "Beta - Two.mp3" + "Beta - Two.lrc".
# A move profile files each single into its own album folder. plan._companions returns at once
# when the folder holds music not moving with this album ("folder holds other music too;
# companion files stay where they are") - before its own rule that a track's same-stem file
# ("01 a.lrc" with "01 a.flac") goes with the track. So every track leaves its lyrics behind:
# after the job the singles have no lyrics in the library, and the old folder keeps orphaned
# .lrc files naming tracks that are gone (a second run cannot reunite them: the files are not
# beside their tracks any more). The same for a folder split into two albums.
# Fix: in a shared folder, still take each moved track's own same-stem companions (.lrc, .cue
# of a single-file rip) - only the folder's shared files stay.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review22a_common import Base, encode, executor, pl, target  # noqa: E402


class MixedFolderLyrics(Base):
    def test_each_single_takes_its_own_lyrics(self):
        s = self.lib / "Singles"
        a = encode(s / "Alpha - One.mp3", 500)
        b = encode(s / "Beta - Two.mp3", 600)
        (s / "Alpha - One.lrc").write_text("[00:00.00]alpha\n")
        (s / "Beta - Two.lrc").write_text("[00:00.00]beta\n")
        prof = self.profile(operation="move", destination=str(self.dest))
        props = [self.proposal(source_dir=str(s), files=[str(f)],
                               targets=[target(1, t, album=t, artist=who, totaltracks=["1"], releasetype=["single"])],
                               release={"album": [t], "albumartist": [who]})
                 for f, t, who in ((a, "One", "Alpha"), (b, "Two", "Beta"))]
        p = pl.build(props, prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        left = sorted(x.name for x in s.iterdir()) if s.exists() else []
        moved = sorted(x.relative_to(self.dest).as_posix() for x in self.dest.rglob("*") if x.is_file())
        self.assertEqual(left, [], f"lyrics left behind, away from their tracks: {left}; library: {moved}")
        self.assertEqual(len([m for m in moved if m.endswith(".lrc")]), 2, moved)


if __name__ == "__main__":
    unittest.main()
