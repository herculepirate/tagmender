# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). Not a regression: the previous version does the same.
# backup_tags keeps an MP3's ID3v2.3/2.4 tag as mutagen re-serialises it and a FLAC file's
# Vorbis comments as f.tags.items() (keys lower-cased, in the order of a set), and restore_tags
# writes those back. A tag TagMender (mutagen) did not write itself - foobar2000's, Mp3tag's,
# EAC's, Picard's own upper-case FLAC fields - comes back with its frames / comments in another
# order (and FLAC field names in lower case): undo is not exact, file by file, for most of a
# library tagged by other programs. The record cannot see it (it is built the same lossy way).
# The ID3v2.2 tag and a FLAC file's ID3 head are already kept as raw bytes (_id3_head,
# "id3_raw"); the ID3v2.3/2.4 tag could be too, and a FLAC file's comments as list(f.tags)
# (the (key, value) pairs in order, keys as stored).
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, bare_mp3, executor, make, pl, target  # noqa: E402

from mutagen.flac import FLAC  # noqa: E402


def syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def fr23(fid, data):
    return fid + struct.pack(">I", len(data)) + b"\0\0" + data


def utf16(s):
    return b"\x01\xff\xfe" + s.encode("utf-16-le") + b"\0\0"


def foobar_v23():
    """An ID3v2.3 tag as foobar2000 writes one: TPE1 first, UTF-16 text, 1 KB of padding."""
    body = fr23(b"TPE1", utf16("Some Artist")) + fr23(b"TALB", utf16("Old Album")) + fr23(b"TIT2", utf16("Old")) + \
        fr23(b"TYER", b"\x002001")
    body += b"\0" * 1024
    return b"ID3\x03\x00\x00" + syncsafe(len(body)) + body


class ForeignTagsReorderedOnUndo(Base):
    def go(self, fs):
        orig = [f.read_bytes() for f in fs]
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(res["job"])
        return orig, out

    def test_mp3_id3v23_by_another_tagger(self):
        fs = [bare_mp3(self.lib / "A" / "x" / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            f.write_bytes(foobar_v23() + f.read_bytes())
        orig, out = self.go(fs)
        self.assertEqual(fs[0].read_bytes()[:200], orig[0][:200], f"the ID3v2.3 tag came back reordered: {out}")
        self.assertEqual([f.read_bytes() for f in fs], orig)

    def test_flac_upper_case_comments(self):
        fs = [make(self.lib / "A" / "y" / f"0{n} t.flac", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            x = FLAC(str(f))
            x.tags.clear()
            for k, v in (("TITLE", "Old"), ("ARTIST", "Some Artist"), ("ALBUM", "Old Album"), ("DATE", "2001"),
                         ("TRACKNUMBER", "1"), ("GENRE", "Rock"), ("GENRE", "Pop")):
                x.tags.append((k, v))
            x.save()
        was = list(FLAC(str(fs[0])).tags)
        orig, out = self.go(fs)
        self.assertEqual(list(FLAC(str(fs[0])).tags), was, f"the Vorbis comments came back changed: {out}")
        self.assertEqual([f.read_bytes() for f in fs], orig)


if __name__ == "__main__":
    unittest.main()
