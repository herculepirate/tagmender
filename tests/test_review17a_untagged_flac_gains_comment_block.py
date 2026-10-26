# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). Not a regression: the previous version does the same.
# The FLAC twin of the sixteenth review's "untagged MP3 gains an ID3 tag on undo". A FLAC file
# with no VORBIS_COMMENT block at all (`metaflac --remove --block-type=VORBIS_COMMENT`, some
# rippers' and converters' output): backup_tags records "tags": None. restore_tags has no
# "it had none" branch for FLAC (only for MP3): it falls through to f.add_tags(),
# f.tags.clear(), f.save() - the file comes back with an empty VORBIS_COMMENT block it never
# had. Undo is not exact even by TagMender's own record: backup_tags of the undone file says
# {"vorbis": []} where the job's backup said None.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, executor, make, pl, ta, target  # noqa: E402

from mutagen.flac import FLAC  # noqa: E402


def untag(p):
    f = FLAC(str(p))
    f.metadata_blocks = [b for b in f.metadata_blocks if b is not f.tags]
    f.tags = None
    f.save()


def key(p):
    return executor._rec_key(ta.backup_tags(p, tempfile.mkdtemp()))


class UntaggedFlacGainsCommentBlock(Base):
    def go(self, **prof):
        alb = self.lib / "Artist" / "Album"
        fs = [make(alb / f"0{n} t.flac", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            untag(f)
        self.assertIsNone(FLAC(str(fs[0])).tags)
        orig, keys = [f.read_bytes() for f in fs], [key(f) for f in fs]
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        p = pl.build([prop], self.profile(**prof))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(res["job"])
        back = [Path(fe["src"]) for fe in p["albums"][0]["files"]]
        self.assertIsNone(FLAC(str(back[0])).tags, f"undo gave the file a Vorbis comment block: {out}")
        self.assertEqual([key(f) for f in back], keys)
        self.assertEqual([f.read_bytes() for f in back], orig)

    def test_in_place(self):
        self.go()

    def test_moved(self):
        self.go(operation="move", destination=str(self.dest))


if __name__ == "__main__":
    unittest.main()
