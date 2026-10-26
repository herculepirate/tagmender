# Twenty-eighth independent review (A): `enrich.py --apply` (ReplayGain, genres, lyrics,
# quality) rewrites every MP3's whole ID3v2.3 tag as ID3v2.4.
# enrich._put ends with mutagen's plain f.save(), which writes ID3v2.4: adding one TXXX
# (REPLAYGAIN_*) or a TCON converts the tag - TYER/TDAT become TDRC, multi-value frames written
# "A/B" by Picard's v2.3 mode become v2.4 null-separated values, the text encoding changes.
# Windows Explorer, many car stereos and older players read only v2.3: after enrich they lose
# the year, and the artists read differently. Picard users choose "ID3v2.3" exactly for them
# (TagMender itself honours write_id3v23 and keeps a v2.3 tag v2.3, tagio.save_id3). The last
# round fixed this for coverup_batch's embed ("in the tag's own version: a v2.3 tag stays
# v2.3"), not for enrich.
# Not a regression (the same in the previous version).
# Fix: in enrich._put save ID3 tags with tagio.save_id3(f, f.tags) (the tag's own version),
# as _embed_one now does.
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402
import enrich as en  # noqa: E402


class EnrichKeepsId3v23(unittest.TestCase):
    def test_replaygain_and_genre_keep_the_v23_tag(self):
        from mutagen.id3 import ID3, TIT2, TPE1, TYER, TCON
        tmp = Path(tempfile.mkdtemp(prefix="tm28A-v23-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        mp3 = c.encode(tmp / "01 Song.mp3")
        t = ID3()
        t.add(TIT2(encoding=1, text=["Song"]))
        t.add(TPE1(encoding=1, text=["Artist A/Artist B"]))
        t.add(TYER(encoding=1, text=["1999"]))
        t.add(TCON(encoding=1, text=["Rock"]))
        t.save(mp3, v2_version=3)
        audio = c.audio_md5(mp3)
        en.put(mp3, {"REPLAYGAIN_TRACK_GAIN": "-3.20 dB", "GENRE": ["Death Metal"]})
        self.assertEqual(c.audio_md5(mp3), audio)
        after = ID3(mp3, translate=False)
        self.assertEqual(after.version, (2, 3, 0), "enrich converted the track's ID3v2.3 tag to ID3v2.4")
        self.assertIn("TYER", after, "the year frame a v2.3 reader looks for is gone")


if __name__ == "__main__":
    unittest.main()
