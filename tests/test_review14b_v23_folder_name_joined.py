# Fourteenth independent review (Picard code): this test failed before its fix.
"""plan.py: the copy/move destination is rendered from `merged` = the file's tags + the
changes - and the changes are now tagio.stored() values, i.e. what an ID3v2.3 tag makes of
them: several values joined with "/". The default naming preset's
$join(%_albumartists_sorted%, & ) then sees one album artist "A/B" (rendered "A_B", "/"
not being allowed in a name) where Picard - which names files from its metadata, not from
the v2.3 frames - and the same album in FLAC give "A & B": an ID3v2.3 MP3 collaboration is
moved into a folder of its own, "A_B", away from the rest of the artists' albums."""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review14b_common as _common  # noqa: E402,F401

import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)


class V23FolderName(Base):
    def folders(self, ext, v23):
        album = self.lib / "In" / f"x{ext[1:]}{v23}"
        files = [audio(album / f"0{i} t{ext}", 440 + 100 * i, title=f"t{i}") for i in (1, 2)]
        if v23:
            from mutagen.id3 import ID3
            for f in files:
                ID3(str(f)).save(str(f), v2_version=3)
        tg = [target(i, f"T{i}", artist="A & B", albumartists=["A", "B"]) for i in (1, 2)]
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": ""})
        p = pl.build([self.proposal(source_dir=str(album), files=[str(f) for f in files], targets=tg)], prof)
        return sorted({str(Path(fe["dst"]).parent.relative_to(self.dest)) for fe in p["albums"][0]["files"]})

    def test_v23_mp3_goes_where_the_flac_goes(self):
        flac = self.folders(".flac", False)
        self.assertEqual(flac, ["A & B/2001 - Real Album"])
        self.assertEqual(self.folders(".mp3", False), flac)
        self.assertEqual(self.folders(".mp3", True), flac, "the ID3v2.3 copy of the album goes elsewhere")


if __name__ == "__main__":
    unittest.main()
