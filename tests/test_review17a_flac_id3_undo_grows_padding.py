# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). Not a regression: the previous version does the same.
# A FLAC file with an ID3v2 tag in front (and an ID3v1 behind), saved with Picard's "Remove ID3
# tags from FLAC files": mutagen's FLAC.save(deleteid3=True) drops the ID3v2 tag and gives its
# bytes to the PADDING block (the audio does not move). Undo (restore_tags) saves the FLAC
# metadata from the record - mutagen keeps that larger padding - and then _put_ends inserts the
# ID3v2 tag in front again. The file comes back larger by the ID3 tag's size, with a PADDING
# block that is not the one it had: undo is not exact (the record does not see it: padding is
# not in it). Undo could put the whole metadata region back as it was (the head blob, the
# FLAC metadata blocks' bytes), or give the padding back its old length.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import V1, Base, executor, make, pl, target  # noqa: E402

from mutagen.flac import FLAC, Padding  # noqa: E402
from mutagen.id3 import ID3, TIT2  # noqa: E402


def id3_head():
    q = Path(tempfile.mkdtemp()) / "h.id3"
    q.write_bytes(b"")
    i = ID3()
    i.add(TIT2(encoding=3, text=["From an old tagger"]))
    i.save(str(q))
    return q.read_bytes()


def padding(p):
    return [b.length for b in FLAC(str(p)).metadata_blocks if isinstance(b, Padding)]


class FlacId3UndoGrowsPadding(Base):
    def test_strip_id3_undone_exactly(self):
        alb = self.lib / "Artist" / "Album"
        fs = [make(alb / f"0{n} t.flac", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            f.write_bytes(id3_head() + f.read_bytes() + V1)
        orig, pad = [f.read_bytes() for f in fs], padding(fs[0])
        prof = self.profile(follow_picard=False, picard={"remove_id3_from_flac": True})
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["files"][0]["format"], {"strip_id3": True})
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual(fs[0].read_bytes()[:4], b"fLaC")
        out = executor.undo(res["job"])
        self.assertEqual(fs[0].read_bytes()[:3], b"ID3", out)
        self.assertEqual(padding(fs[0]), pad, f"undo left the FLAC padding grown by the ID3 tag's size: {out}")
        self.assertEqual([len(f.read_bytes()) for f in fs], [len(o) for o in orig])
        self.assertEqual([f.read_bytes() for f in fs], orig)


if __name__ == "__main__":
    unittest.main()
