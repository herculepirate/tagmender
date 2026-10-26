# Thirtieth independent review (A): tagio.edit_id3 - this round's "edit a frame or two, every
# other frame kept as it is" for enrich, tag_albums --mark-unresolved and write_albumid -
# copies an MP3's ID3v1 tag into its ID3v2 tag and rewrites the ID3v1 tag, losing its comment.
#
# edit_id3 loads ID3(path, translate=False) with mutagen's default load_v1=True: the ID3v1
# title/artist/album/year/track/genre/comment are merged into the ID3v2 tag as frames wherever
# it has none (TYER or TDRC, TRCK, TCON, COMM "ID3v1 Comment"), and t.save(path) (default v1=1)
# regenerates the ID3v1 tag from that merged tag - the ID3v1 comment ("ripped by me") is gone.
# So marking an album unresolved, or enrich adding one TXXX, adds four frames to the ID3v2 tag
# (TagMender then reads a genre, a date, a track number and a described comment the file never
# had in it) and drops the ID3v1 comment. Undo puts it back (the trailer is in the backup), but
# until then the library is changed in fields the run does not show.
# Not a regression (the previous version's mutagen.File + save did the same).
# Fix: load with load_v1=False, and keep the file's ID3v1 trailer as it was (save with v1=1
# regenerates it: put the original 128 bytes back after saving, as tag_albums._put_ends does,
# or save v1=0 and append the old trailer).
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import tag_albums as ta  # noqa: E402
from mutagen.id3 import ID3, TALB, TIT2, TPE1  # noqa: E402


def v1_tag(title, artist, album, year, comment, track, genre):
    pad = lambda s, n: s.encode("latin1")[:n].ljust(n, b"\0")     # noqa: E731
    return b"TAG" + pad(title, 30) + pad(artist, 30) + pad(album, 30) + pad(year, 4) + pad(comment, 28) + \
        b"\0" + bytes([track, genre])


def make(path, version):
    f = c.encode(path, 440, 1)
    t = ID3()
    t.add(TIT2(encoding=0, text=["Song"]))
    t.add(TPE1(encoding=0, text=["Band"]))
    t.add(TALB(encoding=0, text=["Album"]))
    t.save(str(f), v2_version=version, v1=0)
    with open(f, "ab") as fh:                      # an ID3v1 tag with more in it than the ID3v2 tag
        fh.write(v1_tag("Song", "Band", "Album", "1999", "ripped by me", 3, 0))
    return f


def frames(p):
    t = ID3(str(p), translate=False, load_v1=False)
    return t.version[:2], sorted(k for k in t.keys() if not k.startswith("TXXX"))


class EditId3KeepsId3v1(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm30A-v1-"))

    def _check(self, f, write):
        before, trailer = frames(f), f.read_bytes()[-128:]
        write(f)
        self.assertEqual(frames(f), before, "frames the writer does not write were added to the ID3v2 tag "
                                            "(from the ID3v1 tag)")
        self.assertEqual(f.read_bytes()[-128:], trailer, "the ID3v1 tag was rewritten (its comment lost)")

    def test_mark_unresolved_v23(self):
        self._check(make(self.tmp / "a.mp3", 3), lambda f: ta.write_tagmender(f, "Unresolved", "nothing found"))

    def test_mark_unresolved_v24(self):
        self._check(make(self.tmp / "b.mp3", 4), lambda f: ta.write_tagmender(f, "Unresolved", "nothing found"))

    def test_write_albumid(self):
        self._check(make(self.tmp / "c.mp3", 3), lambda f: ta.write_albumid(f, "0f0e0d0c-0000-0000-0000-000000000000"))

    def test_enrich(self):
        self._check(make(self.tmp / "d.mp3", 3),
                    lambda f: en.put(f, {"MUSICBRAINZ_RELEASEGROUPID": "0f0e0d0c-0000-0000-0000-000000000000"}))


if __name__ == "__main__":
    unittest.main()
