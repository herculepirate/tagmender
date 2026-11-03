# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). A regression: passes with TM_REPO=prev.
# Today's M4A record is tag-level (tag_albums._backup_raw: {"tags": {"mp4": [...]}, "padding"}).
# For an M4A file with NO tag at all (no moov/udta/meta/ilst - as MP4Box, afconvert, many
# encoders and stream downloads leave it), mutagen's f.tags is None: _backup_raw returns
# {"tags": None} and no "padding". restore_tags then falls through to the generic end:
# f.add_tags(); f.tags.clear(); f.save() - with mutagen's default padding. Undo leaves the file
# with a new udta/meta/hdlr/ilst and a ~1.6 KB "free" atom it never had:
#  - undo is not exact (the release notes' only M4A limit is "8 bytes larger"); with the moov
#    in front of the audio (faststart) every chunk offset is rewritten too;
#  - the file no longer matches its own backup record ({"tags": None} vs {"tags": {"mp4": []},
#    "padding": ...}): an undo cut short after putting such a file back, then run again, refuses
#    it - "its tags or pictures were edited after the job" - though nobody touched it.
# Related (test_tags_in_another_order): any M4A whose ilst items are not in mutagen's own order
# comes back rewritten in mutagen's order - not the 8-byte limit the release notes give either.
# The previous version kept the moov whole and put it back byte for byte.
# Fix: for an MP4 with no ilst, record that ("mp4_none") and on undo remove the meta atom (or
# the udta the job created) as it was - e.g. keep the old moov blob for this case only and put
# it back when only udta/meta differ; at least save with padding 0 and treat "no tags" and
# "an empty ilst" alike in _same_backup.
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, atoms, decoded, encode, executor, pl, run_and_undo, target  # noqa: E402


def strip_udta(f):
    """The file as an encoder that writes no tags leaves it: no moov/udta (moov is last)."""
    data = bytearray(f.read_bytes())
    moov = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"moov" and d == 0)
    assert moov[0] + moov[1] == len(data)
    udta = next((p, n) for k, p, n, d in atoms(bytes(data)) if k == b"udta" and d == 1)
    del data[udta[0]:udta[0] + udta[1]]
    data[moov[0]:moov[0] + 4] = struct.pack(">I", moov[1] - udta[1])
    f.write_bytes(bytes(data))


class M4aWithoutTagsUndoNotExact(Base):
    def files(self):
        from mutagen.mp4 import MP4
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]
        for f in fs:
            strip_udta(f)
            self.assertIsNone(MP4(f).tags)
        return fs

    def test_undo_gives_back_the_file(self):
        fs = self.files()
        orig = [f.read_bytes() for f in fs]
        played = [decoded(f) for f in fs]
        _res, out = run_and_undo(self, fs)
        for f, o, want in zip(fs, orig, played):
            self.assertEqual(decoded(f), want)
            self.assertEqual(len(f.read_bytes()) - len(o), 0, f"{f.name}: undo left it larger: {out}")
            self.assertEqual(f.read_bytes(), o, f"{f.name}: not as it was after undo: {out}")

    def test_undo_cut_short_then_run_again(self):
        fs = self.files()
        orig = [f.read_bytes() for f in fs]
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        real = executor.Journal.write

        def write(j, **e):                     # the power goes right after the first file is put back
            if e.get("ev") == "undo_step" and e.get("key", [None])[0] == "file":
                raise KeyboardInterrupt
            return real(j, **e)

        with mock.patch.object(executor.Journal, "write", write), self.assertRaises(KeyboardInterrupt):
            executor.undo(res["job"])
        out = executor.undo(res["job"])
        self.assertFalse([x for x in out if x.startswith("PROBLEM")], out)
        self.assertEqual([len(f.read_bytes()) for f in fs], [len(o) for o in orig], out)

    @unittest.skip("known limit: mutagen writes MP4 tags in its own order (release notes)")
    def test_tags_in_another_order(self):
        # (related, same cause - the tag-level M4A record: mutagen writes the ilst items in its own
        # order, so a file whose tagger wrote them in another - iTunes, foobar2000, ffmpeg -
        # comes back with its tag rewritten, and the free atom moved behind it)
        from mutagen.mp4 import MP4
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]
        for f in fs:
            m = MP4(f)
            m["\xa9nam"], m["\xa9ART"], m["\xa9alb"] = ["orig"], ["me"], ["mine"]
            m.save(padding=lambda i: 2000)
            data = bytearray(f.read_bytes())
            at = bytes(data).find(b"ilst") - 4                                    # (moov/udta/meta/ilst)
            ilst = (at, struct.unpack(">I", data[at:at + 4])[0])
            items, p = [], ilst[0] + 8
            while p < ilst[0] + ilst[1]:
                n = struct.unpack(">I", data[p:p + 4])[0]
                items.append(bytes(data[p:p + n]))
                p += n
            data[ilst[0] + 8:ilst[0] + ilst[1]] = b"".join(reversed(items))      # (another tagger's order)
            f.write_bytes(bytes(data))
            self.assertEqual(MP4(f)["\xa9nam"], ["orig"])
        orig = [f.read_bytes() for f in fs]
        _res, out = run_and_undo(self, fs)
        for f, o in zip(fs, orig):
            self.assertEqual(f.read_bytes(), o, f"{f.name}: not as it was after undo: {out}")


if __name__ == "__main__":
    unittest.main()
