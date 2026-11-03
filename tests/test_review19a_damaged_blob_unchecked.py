# Nineteenth independent review (A): this test failed before its fix.
# Nineteenth independent review (A). test_m4a_cover: not a regression (the previous version
# checked no blob at all and wrote its damaged moov blob); test_flac_picture...: a regression
# (prev put the whole FLAC head back and never read the picture blob). Today's release notes
# promise "Backup data is ... checked before
# use: damaged data is never written into a file", and only _put_head / _put_ends / _put_moov /
# _flac_raw_fits read their blobs through tag_albums._read_blob (sha1 == name). restore_tags
# still reads these blobs raw, unchecked (tag_albums.py restore_tags):
#  - an M4A file's cover art and freeform values (MusicBrainz IDs, iTunNORM ...): line ~610,
#    (Path(blob_dir) / v["bytes"]).read_bytes() - and today EVERY M4A record is tag-level, so
#    this is the path for every M4A file;
#  - the ID3 tag of a WAV / AIFF / DSF file (tag_albums runs: enrich.py, web_tag.py): line ~574;
#  - a FLAC file's pictures on the tag-level path (a cue sheet added since): line ~566.
# A damaged blob (a bad sector, a bit flipped, a copy of the state folder cut short) is written
# into the file and undo reports "put back": a corrupt cover or garbage in a tag, silently.
# Fix: read every blob through _read_blob (and refuse the file, leaving it as it is, on a
# mismatch), before anything is written.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review19a_common import Base, embed_cuesheet, encode, executor, run_and_undo  # noqa: E402


def bit_rot(job, marker):
    """One byte changed in every blob of the job holding marker (its name - its sha1 - stays)."""
    hit = 0
    for b in (executor.job_dir(job) / "blobs").iterdir():
        data = bytearray(b.read_bytes())
        at = bytes(data).find(marker)
        if at >= 0:
            data[at] ^= 0x20
            b.write_bytes(bytes(data))
            hit += 1
    assert hit, "no blob holds the marker"


def problem_for(f, out):
    return any(x.startswith("PROBLEM") and f.name in x for x in out)


class DamagedBlobUnchecked(Base):
    def test_m4a_cover(self):
        from mutagen.mp4 import MP4, MP4Cover
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]
        cover = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"COVERDATA" * 50 + b"\xff\xd9"
        for f in fs:
            m = MP4(f)
            m["covr"] = [MP4Cover(cover, MP4Cover.FORMAT_JPEG)]
            m.save()
        _res, out = run_and_undo(self, fs, between=lambda job: bit_rot(job, b"COVERDATA"))
        for f in fs:
            if not problem_for(f, out):
                self.assertEqual(bytes(MP4(f)["covr"][0]), cover,
                                 f"{f.name}: undo wrote a damaged backup into the file and said: {out}")

    def test_flac_picture_on_the_tag_level_path(self):
        from mutagen.flac import FLAC, Picture
        fs = [self.f1, self.f2]
        img = b"\x89PNG\r\n\x1a\n" + b"BACKCOVER" * 50
        for f in fs:
            fl = FLAC(f)
            pic = Picture()
            pic.type, pic.mime, pic.data = 4, "image/png", img        # (a back cover: the job keeps it)
            fl.add_picture(pic)
            fl.save()

        def between(job):
            for f in fs:                       # a cue sheet embedded since: undo takes the tag-level path
                embed_cuesheet(f)
            for b in (executor.job_dir(job) / "blobs").iterdir():
                data = b.read_bytes()
                if not data.startswith(b"fLaC") and b"BACKCOVER" in data:     # the picture's own blob
                    at = data.find(b"BACKCOVER")
                    b.write_bytes(data[:at] + b"b" + data[at + 1:])

        _res, out = run_and_undo(self, fs, between=between)
        for f in fs:
            if not problem_for(f, out):
                self.assertEqual([p.data for p in FLAC(f).pictures], [img],
                                 f"{f.name}: undo wrote a damaged backup into the file and said: {out}")


if __name__ == "__main__":
    unittest.main()
