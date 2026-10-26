# Fifteenth independent review (B): this test failed before its fix.
"""tagio.save_id3 saves with mutagen's default v1=1: an MP3's existing ID3v1 tag is rewritten
from the new ID3v2 frames. The job's tag backup (tag_albums.backup_tags) keeps the ID3v2 tag
only, and restore_tags saves it back with v1=1 too - so undo rebuilds the ID3v1 tag from the
restored ID3v2 tag instead of putting the file's own ID3v1 tag back: its title, artist, year
(here "V1 only title", "V1 artist", 1999) are lost. Undo is not exact."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)

V1 = (b"TAG" + b"V1 only title".ljust(30, b"\0") + b"V1 artist".ljust(30, b"\0") + b"\0" * 30 + b"1999"
      + b"\0" * 30 + b"\xff")


class UndoId3v1(Base):
    def roundtrip(self, version):
        from mutagen.id3 import ID3, TIT2
        album = self.lib / "In" / f"v{version}"
        files = [audio(album / f"0{i} t.mp3", 440 + 100 * i) for i in (1, 2)]
        for f in files:
            t = ID3()
            t.add(TIT2(text=["V2 title"]))
            t.save(str(f), v1=0, v2_version=version)
            with open(f, "ab") as fh:
                fh.write(V1)
        before = [f.read_bytes() for f in files]
        p = pl.build([self.proposal(source_dir=str(album), files=[str(f) for f in files],
                                    targets=[target(1, "T1"), target(2, "T2")])], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        executor.undo(p["job"])
        self.files = files
        return before, [f.read_bytes() for f in files]

    def test_undo_puts_the_id3v1_tag_back_v24(self):
        before, after = self.roundtrip(4)
        self.assertEqual(after[0][-128:], before[0][-128:], "the file's own ID3v1 tag is gone after undo")
        self.assertEqual([x[-128:] for x in after], [x[-128:] for x in before])
        from mutagen.id3 import ID3
        self.assertEqual([str(ID3(f, load_v1=False).get("TIT2")) for f in self.files], ["V2 title"] * 2)

    def test_undo_puts_the_id3v1_tag_back_v23(self):
        before, after = self.roundtrip(3)
        self.assertEqual(after[0][-128:], before[0][-128:], "the file's own ID3v1 tag is gone after undo")
        self.assertEqual([x[-128:] for x in after], [x[-128:] for x in before])
        from mutagen.id3 import ID3
        self.assertEqual([str(ID3(f, load_v1=False).get("TIT2")) for f in self.files], ["V2 title"] * 2)


if __name__ == "__main__":
    unittest.main()
