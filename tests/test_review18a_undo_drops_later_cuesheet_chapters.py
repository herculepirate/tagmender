# Eighteenth independent review (A): this test failed before its fix.
# Eighteenth independent review (A). A regression: passes with TM_REPO=prev.
# Undo leaves files edited after the job alone - "edited" meaning their tags or pictures
# changed (tags_sig / pics_sig). Today's records put back a FLAC file's WHOLE metadata
# (flac_raw: STREAMINFO ... PADDING) and an MP4 file's WHOLE moov atom, so whatever else the user
# added after the job is silently thrown away by a plain `tagmender undo` (no --force, nothing
# reported, "put back"):
#  - a FLAC cue sheet embedded after the job (metaflac --import-cuesheet-from, foobar2000,
#    CUETools), seek points added (metaflac --add-seekpoint), APPLICATION blocks;
#  - an M4A/M4B file's chapters added after the job (Nero "chpl" in moov/udta: mp4chaps,
#    Audiobook tools, foobar2000).
# Neither is in tags_sig, so the verdict never sees the edit; _restore's "same as backed up?"
# check says "different" and rewrites the file. The previous version wrote only the comments
# and pictures (FLAC) or the ilst (MP4) and kept the user's cue sheet and chapters.
# Fix: either take these into the "edited after the job" verdict (compare the file's metadata
# outside the tags with what the job left - e.g. record a hash of the job's resulting head /
# moov in the "committed" event and refuse without --force when it differs), or restore raw
# only when the file's head / moov is still byte-for-byte what the job wrote, falling back to
# the tag-level restore otherwise.
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review18a_common import Base, encode, run_and_undo  # noqa: E402


def embed_cuesheet(f):
    from mutagen.flac import FLAC, CueSheet, CueSheetTrack, CueSheetTrackIndex
    fl = FLAC(f)
    cs = CueSheet(None)
    cs.media_catalog_number = b"0724384260521"
    t1 = CueSheetTrack(1, 0, b"GBAYE0000001")
    t1.indexes = [CueSheetTrackIndex(1, 0)]
    cs.tracks = [t1, CueSheetTrack(170, fl.info.total_samples)]
    fl.metadata_blocks.append(cs)
    fl.save()


def add_nero_chapters(f, titles):
    """moov/udta/chpl appended (the moov is the file's last atom: no offsets move)."""
    data = bytearray(f.read_bytes())
    pos, moov = 0, None
    while pos + 8 <= len(data):
        n, k = struct.unpack(">I4s", data[pos:pos + 8])
        if k == b"moov":
            moov = pos
        pos += n
    assert moov is not None and moov + struct.unpack(">I", data[moov:moov + 4])[0] == len(data)
    p, end, udta = moov + 8, len(data), None
    while p + 8 <= end:
        n, k = struct.unpack(">I4s", data[p:p + 8])
        if k == b"udta":
            udta = p
        p += n
    body = b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00" + bytes([len(titles)])
    for i, t in enumerate(titles):
        body += struct.pack(">Q", i * 5000000) + bytes([len(t)]) + t.encode()
    chpl = struct.pack(">I4s", 8 + len(body), b"chpl") + body
    un = struct.unpack(">I", data[udta:udta + 4])[0]
    data[udta + un:udta + un] = chpl
    data[udta:udta + 4] = struct.pack(">I", un + len(chpl))
    data[moov:moov + 4] = struct.pack(">I", struct.unpack(">I", data[moov:moov + 4])[0] + len(chpl))
    f.write_bytes(bytes(data))


class UndoDropsLaterCuesheetChapters(Base):
    def test_flac_cuesheet_added_after_job(self):
        from mutagen.flac import FLAC
        fs = [self.f1, self.f2]

        def between(job):
            for f in fs:
                embed_cuesheet(f)

        _res, out = run_and_undo(self, fs, between=between)
        for f in fs:
            if any(x.startswith("PROBLEM") and f.name in x for x in out):
                continue                                  # (left alone and said so: fine)
            self.assertIn("CueSheet", [type(b).__name__ for b in FLAC(f).metadata_blocks],
                          f"{f.name}: the cue sheet embedded after the job is gone, unreported: {out}")

    def test_m4a_chapters_added_after_job(self):
        from mutagen.mp4 import MP4
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]

        def between(job):
            for f in fs:
                add_nero_chapters(f, ["Intro", "Part two"])
                self.assertEqual([c.title for c in MP4(f).chapters], ["Intro", "Part two"])

        _res, out = run_and_undo(self, fs, between=between)
        for f in fs:
            if any(x.startswith("PROBLEM") and f.name in x for x in out):
                continue
            ch = MP4(f).chapters
            self.assertEqual([c.title for c in ch] if ch else [], ["Intro", "Part two"],
                             f"{f.name}: the chapters added after the job are gone, unreported: {out}")


if __name__ == "__main__":
    unittest.main()
