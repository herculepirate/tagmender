# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). Not a regression: the previous version leaves the file
# larger still (a whole new tag with mutagen's padding).
# Today's record for an M4A with no tags keeps only whether its moov had a "udta" atom
# (_backup_raw: rec["mp4_udta"] = _udta(fh) is not None), and undo takes the job's udta out only
# when there was none (_drop_udta). An M4A whose udta holds something else but no meta/ilst - a
# title "name" atom, Nero/QuickTime chapters ("chpl"), a camera's or an editor's own atoms -
# has mutagen's f.tags None, so it is recorded {"tags": None, "mp4_udta": True}; the job adds
# meta/hdlr/ilst inside that udta, and undo (f.delete()) leaves an empty meta/hdlr/ilst behind:
#  - the file comes back 61 bytes larger, with the moov LAST (audio first) - the case the release
#    notes promise is exact ("one with no tags at all and the audio first keeps an empty tag" is
#    the only no-tag limit given), and the undo says "put back".
# Fix: record whether the udta held a meta atom (e.g. rec["mp4_meta"]), and on undo cut out
# just the meta atom the job added (as _drop_udta does for the udta, same offset conditions).
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import Base, atoms, decoded, encode, executor, pl, target  # noqa: E402


def udta_name_only(f):
    """The file with a udta holding only a "name" atom (no meta, no ilst); moov is last."""
    data = bytearray(f.read_bytes())
    moov = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"moov" and d == 0)
    assert moov[0] + moov[1] == len(data)
    udta = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"udta" and d == 1)
    name = struct.pack(">I4s", 8 + 5, b"name") + b"hello"
    new = struct.pack(">I4s", 8 + len(name), b"udta") + name
    data[udta[0]:udta[0] + udta[1]] = new
    data[moov[0]:moov[0] + 4] = struct.pack(">I", moov[1] - udta[1] + len(new))
    f.write_bytes(bytes(data))


class UdtaWithoutMeta(Base):
    def test_undo_gives_back_the_file(self):
        from mutagen.mp4 import MP4
        f = encode(self.lib / "A" / "m" / "01 t.m4a", 440, "-c:a", "aac")
        udta_name_only(f)
        self.assertIsNone(MP4(f).tags)
        orig, played = f.read_bytes(), decoded(f)
        prop = self.proposal(source_dir=str(f.parent), files=[str(f)], targets=[target(1, "One")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        out = executor.undo(res["job"])
        self.assertEqual(decoded(f), played)
        self.assertEqual(len(f.read_bytes()) - len(orig), 0, f"undo left it larger: {out}")
        self.assertEqual(f.read_bytes(), orig, f"not as it was after undo: {out}")


if __name__ == "__main__":
    unittest.main()
