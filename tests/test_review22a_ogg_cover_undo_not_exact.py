# Twenty-second independent review (A).
# An Ogg Vorbis album with no cover: the plan embeds the found front cover (artwork
# "embed_front", the default) and writes the tags. The picture (a METADATA_BLOCK_PICTURE
# comment, tens of KB) makes the comment packet span many Ogg pages, so mutagen repaginates the
# header pages. Undo puts the comments back (tag_albums.restore_tags, "vorbis_raw" with the old
# padding) but not the header pages as ffmpeg/the ripper laid them out: the file comes back
# with the same comments and audio but different bytes (size differs) - the undo of a job is
# not exact, though it reports "put back". Opus files of the same album come back exactly.
# Without the cover (tags only) the Ogg file does come back exactly.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review22a_common import Base, decoded, encode, executor, jpeg, pl, target  # noqa: E402


class OggCoverUndo(Base):
    def go(self, ext, art):
        d = self.lib / "Artist B" / "2010 - The Box"
        fs = [encode(d / f"0{n} t{ext}", 300 + 50 * n, meta={"title": f"t{n}", "artist": "Artist B"}) for n in (1, 2)]
        orig = [f.read_bytes() for f in fs]
        played = [decoded(f) for f in fs]
        kw = {"artwork": {"front": str(jpeg(self.tmp / "staged" / "front.jpg", (100, 50, 25))), "source": "test"}} \
            if art else {}
        prop = self.proposal(source_dir=str(d), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")], **kw)
        p = pl.build([prop], self.profile())
        if art:
            self.assertEqual(sorted(p["albums"][0]["artwork"]["embed_into"]), sorted(map(str, fs)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if "PROBLEM" in o], out)
        self.assertEqual([decoded(f) for f in fs], played)
        self.assertEqual([len(f.read_bytes()) for f in fs], [len(o) for o in orig], f"not as it was: {out}")
        self.assertEqual([f.read_bytes() for f in fs], orig, f"not as it was: {out}")

    def test_ogg_with_cover_embedded(self):
        self.go(".ogg", art=True)

    def test_ogg_tags_only(self):            # (passes: control)
        self.go(".ogg", art=False)

    def test_opus_with_cover_embedded(self):  # (passes: control)
        self.go(".opus", art=True)


if __name__ == "__main__":
    unittest.main()
