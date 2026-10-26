# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). Not a regression: the previous version does the same.
# M4A, Ogg Vorbis and Opus files are backed up as their tag values only (backup_tags "mp4" /
# "vorbis") and put back with mutagen's save. The job's write grew the file's padding (MP4
# "free" atom, the Ogg comment packet's padding); restore_tags writes the old, smaller tag into
# that space and mutagen keeps the padding: every such file comes back larger than it was
# (1-1.5 KB here) - undo of a tag job is not exact for these formats, though the record says it
# is. Undo could restore the exact byte range of the tag (moov/udta/meta and the free atom
# after it; the Ogg comment packet's pages), or give mutagen a padding function that brings
# the padding back to its old length.
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, executor, make, pl, target  # noqa: E402


def encode(path, freq, *codec):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    *codec, "-map_metadata", "-1", "-y", str(path)], check=True)
    return path


class M4aOggUndoNotExact(Base):
    def go(self, fs):
        orig = [f.read_bytes() for f in fs]
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(res["job"])
        self.assertEqual([len(f.read_bytes()) for f in fs], [len(o) for o in orig], f"not put back as it was: {out}")
        self.assertEqual([f.read_bytes() for f in fs], orig)

    def test_m4a(self):
        self.go([make(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n) for n in (1, 2)])

    def test_ogg_vorbis(self):
        self.go([make(self.lib / "A" / "o" / f"0{n} t.ogg", 440 + 100 * n) for n in (1, 2)])

    def test_opus(self):
        self.go([encode(self.lib / "A" / "p" / f"0{n} t.opus", 440 + 100 * n, "-c:a", "libopus") for n in (1, 2)])


if __name__ == "__main__":
    unittest.main()
