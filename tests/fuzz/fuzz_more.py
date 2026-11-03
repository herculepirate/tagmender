# fuzz_crash.py's kill points over more scenarios: junk-only folders, created covers and
# lyrics on a move and a copy, hard-linked copies, a disc set with a per-disc cue sheet.
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuzz_crash as fc  # noqa: E402
from fuzz_crash import pl, target, audio  # noqa: E402


class FuzzMore(fc.Fuzz):
    def s_move_junk(self):
        (self.album / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        (self.album.parent / "Thumbs.db").write_bytes(b"thumbs")
        return pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))

    def s_move_cover_lrc(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        return pl.build([self.proposal(artwork={"front": str(front), "source": "t"},
                                       sidecars={str(self.f1): "[00:00.00]la"})],
                        self.profile(operation="move", destination=str(self.dest),
                                     artwork={"enabled": True, "embed_front": True, "files": True}))

    def s_copy_cover_lrc(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        return pl.build([self.proposal(artwork={"front": str(front), "source": "t"},
                                       sidecars={str(self.f1): "[00:00.00]la"})],
                        self.profile(operation="copy", destination=str(self.dest),
                                     artwork={"enabled": True, "embed_front": True, "files": True}))

    def s_relink_copy(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        for fe in p0["albums"][0]["files"]:
            d = Path(fe["dst"])
            d.parent.mkdir(parents=True, exist_ok=True)
            os.link(fe["src"], d)
        return pl.build([self.proposal()], prof)

    def s_move_discs(self):
        import shutil
        box = self.lib / "Band" / "Box"
        f1 = audio(box / "CD1" / "01.flac", 500)
        f2 = audio(box / "CD2" / "01.flac", 600)
        (box / "cover.jpg").write_bytes(b"jpg")
        (box / "CD1" / "cd1.cue").write_text('FILE "01.flac" WAVE\n')
        shutil.rmtree(self.lib / "Some Artist")
        prop = self.proposal(source_dir=str(box), files=[str(f1), str(f2)],
                             targets=[target(1, "A", discnumber=["1"], totaldiscs=["2"]),
                                      target(1, "B", discnumber=["2"], totaldiscs=["2"])])
        return pl.build([prop], self.profile(operation="move", destination=str(self.dest)))

    SCEN = [s for s in os.environ.get("SCEN_MORE", "s_move_junk,s_move_cover_lrc,s_copy_cover_lrc,s_relink_copy,"
                                                 "s_move_discs").split(",")]

    def test_fuzz(self):
        os.environ.setdefault("SCEN", ",".join(self.SCEN))
        super().test_fuzz()


if __name__ == "__main__":
    import unittest
    unittest.main(defaultTest="FuzzMore")
