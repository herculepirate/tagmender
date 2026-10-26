# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). New in this version (fix_missing_seekpoints_flac; with
# TM_REPO=prev the profile is refused: the setting is unknown there).
# Picard's "Fix missing seekpoints for FLAC files" removes an empty SEEKTABLE block; backup_tags
# records only that there was one ("empty_seektable") and restore_tags puts it back at
# metadata_blocks index 1, right after STREAMINFO, wherever it was. Encoders and taggers that
# write the empty seek table after the VORBIS_COMMENT (or after an APPLICATION block) get
# their file back with its metadata blocks in another order: undo is not exact (the file's
# bytes differ; a seeding torrent or a checksum list no longer matches), and the record does
# not notice it (the blob keys are equal), so a second undo does not either.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, executor, make, pl, target  # noqa: E402

from mutagen.flac import FLAC, SeekTable  # noqa: E402


def blocks(p):
    return [type(b).__name__ for b in FLAC(str(p)).metadata_blocks]


class SeektableUndoMovesBlock(Base):
    def test_empty_seektable_after_the_comments_comes_back_there(self):
        alb = self.lib / "Artist" / "Album"
        fs = [make(alb / f"0{n} t.flac", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            x = FLAC(str(f))
            x.metadata_blocks = [b for b in x.metadata_blocks if not isinstance(b, SeekTable)]
            x.seektable = SeekTable(b"")
            x.metadata_blocks.insert([i for i, b in enumerate(x.metadata_blocks) if b is x.tags][0] + 1, x.seektable)
            x.save()
        was = blocks(fs[0])
        self.assertEqual(was, ["StreamInfo", "VCFLACDict", "SeekTable", "Padding"])
        orig = [f.read_bytes() for f in fs]
        prof = self.profile(follow_picard=False, picard={"fix_missing_seekpoints_flac": True})
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], prof)).run()
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(res["job"])
        self.assertEqual(blocks(fs[0]), was, f"undo put the seek table elsewhere: {out}")
        self.assertEqual([f.read_bytes() for f in fs], orig)


if __name__ == "__main__":
    unittest.main()
