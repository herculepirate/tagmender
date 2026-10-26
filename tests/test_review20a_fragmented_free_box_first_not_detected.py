# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). A regression in tagio.fragmented: test_detected passes with
# TM_REPO=prev (it scanned every .m4a's boxes); the other two fail there too, differently (no
# guard in the writers; the plan dropped the file - review 19).
# tagio.fragmented now decides "by content", but only when the file's FIRST box is ftyp
# (`fh.read(8)[4:8] != b"ftyp": return False`). ISO BMFF allows other boxes before it (a "free"
# or "skip" box left by an editor or a downloader that reserves room, "wide" from QuickTime
# tools), and mutagen opens such a file as MP4 all the same (MP4.score looks for "ftyp" anywhere
# in the first bytes). The previous version scanned every .m4a/.mp4 by its boxes and found the
# moof boxes; today such a fragmented file is taken as an ordinary MP4:
#  - tag_albums.write_albumid / write_tagmender (the Picard-run seeds, --mark-unresolved),
#    enrich.put, coverup_batch.embed_front and web_tag.write_tags write it through mutagen with
#    no audio check: it no longer plays from its second fragment on, and they report success;
#  - the plan tags it (no "as_is", no note); executor's own check (payload.digest falls back to
#    ffmpeg for a file not starting with ftyp) catches the damage, so the album fails ("audio
#    data differed after writing tags") and is rolled back on every run - it never settles,
#    though the files would just go "as is".
# Fix: in tagio.fragmented, skip leading free/skip/wide boxes (or look for ftyp within the first
# boxes, as mutagen does) - or call tag_albums._fragmented for every file mutagen opens as MP4.
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, atoms, decoded, encode, executor, pl, ta, tagio, target  # noqa: E402



def free_first(data: bytes) -> bytes:
    """data with an 8-byte free box before its ftyp, every fragment's absolute base data offset
    (tfhd) moved with it: the same file, playable."""
    out = bytearray(data)
    for k, p, n, _d in atoms(data):
        if k == b"traf":
            for k2, p2, n2, _d2 in atoms(data, p + 8, p + n):
                if k2 == b"tfhd" and data[p2 + 11] & 1:            # base-data-offset-present
                    off = struct.unpack(">Q", data[p2 + 16:p2 + 24])[0]
                    out[p2 + 16:p2 + 24] = struct.pack(">Q", off + 8)
    return b"\x00\x00\x00\x08free" + bytes(out)


class FreeBoxFirst(Base):
    def make(self, name):
        f = encode(self.tmp / "x" / name / "01 t.m4a", 440, *FRAGS, duration=3)
        g = self.lib / "Some Artist" / name / "01 t.m4a"
        g.parent.mkdir(parents=True)
        g.write_bytes(free_first(f.read_bytes()))                # (a free box before ftyp)
        played = decoded(f)
        self.assertEqual(decoded(g), played, "(the test file itself plays)")
        return g, played

    def test_detected(self):                    # (passes with TM_REPO=prev)
        g, _played = self.make("detected")
        self.assertTrue(tagio.fragmented(g), "a fragmented MP4 with a free box first is not taken as fragmented")

    def test_writers_leave_it_playable(self):
        import enrich
        for name, write in (("albumid", lambda g: ta.write_albumid(g, "0f8a7c0e-1111-2222-3333-444455556666")),
                            ("enrich", lambda g: enrich.put(g, {"LYRICS": "la la " * 300}))):
            g, played = self.make(name)
            try:
                write(g)
            except Exception:
                pass                                          # (refused: fine)
            self.assertEqual(decoded(g), played, f"{name}: a fragmented MP4 written to; it no longer plays "
                                                 f"(fragmented() = {tagio.fragmented(g)})")

    def test_plan_takes_it_as_is(self):
        g, played = self.make("plan")
        prop = self.proposal(source_dir=str(g.parent), files=[str(g)], targets=[target(1, "First")])
        p = pl.build([prop], self.profile())
        a = p["albums"][0]
        res = executor.Executor(p).run()
        self.assertTrue(a["files"] and a["files"][0].get("as_is"),
                        f"planned as an ordinary MP4 (op {a['files'][0]['op']}); the job: {res.get('albums')}")
        self.assertEqual(decoded(g), played)


if __name__ == "__main__":
    unittest.main()
