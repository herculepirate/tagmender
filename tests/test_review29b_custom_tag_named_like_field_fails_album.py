# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression; the
# MP3 cases fail with the review-20, -24 and -27 snapshots as well).
"""An album cannot be tagged when its files carry a user/custom tag named like one of the
fields TagMender writes in a standard place, and its value differs from the release's:

  MP3/WAV/AIFF  TXXX:TOTALTRACKS, TXXX:TOTALDISCS, TXXX:ALBUMARTISTSORT, TXXX:TITLE, ...
  WavPack       an APEv2 item TOTALTRACKS (beside Track)
  M4A           ----:com.apple.iTunes:TOTALTRACKS (beside trkn)
  also          TXXX:RELEASETYPE (Mp3tag/foobar2000's name) and an APEv2 "ALBUMARTIST" item

Every run fails with "track01.mp3: tag totaltracks did not read back as written; album put
back as it was" (or "tag albumartistsort ...").

These are common in real libraries: ffmpeg (and the converters built on it) turns a
Picard-tagged FLAC into an MP3 with TXXX:TOTALTRACKS, TXXX:TOTALDISCS, TXXX:ALBUMARTISTSORT
(and TXXX:TRACKTOTAL, TXXX:DISCTOTAL), and into a WavPack with APE TOTALTRACKS/TOTALDISCS
(checked with this machine's ffmpeg); older Picard versions and other taggers wrote
TXXX:ALBUMARTISTSORT for the album artist sort. The run fails as soon as such a value
differs from MusicBrainz (another edition's track count, a corrected sort name) - exactly
when the album needs re-tagging.

Picard 3.0.2 tags these files normally (checked end to end with Picard's own code):
  TXXX:TOTALTRACKS=9 + TRCK "1": TRCK "1/2", TXXX:TOTALTRACKS kept as it is (a custom tag
    "TOTALTRACKS" - Picard's names are case-sensitive);
  TXXX:ALBUMARTISTSORT: TSO2 written and TXXX:ALBUMARTISTSORT deleted (id3.py
    _save_standard_tag: `elif frameid == 'TSO2': tags.delall('TXXX:ALBUMARTISTSORT')`);
  APE TOTALTRACKS=9 + Track "1": Track "1/2", TOTALTRACKS kept.

Cause: tagio.read lower-cases a user tag's name and merges it into the standard field
(ID3: tagio.py:409-416, `name = _ID3_FREE_R.get(desc.lower(), desc.lower())`; APEv2/MP4
likewise), so the file appears to hold two values for totaltracks / albumartistsort. The plan
writes the standard frame only, the user tag stays, the read-back check (executor.py:1173-1176)
sees both values and rolls the album back.

Fix: read a user tag whose lower-cased name is a field kept in a standard frame/atom/item as
a custom tag of its own spelling (as Picard does), not as that field; and, as Picard, delete
TXXX:ALBUMARTISTSORT when TSO2 is written.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TPOS, TRCK, TXXX
from mutagen.mp4 import MP4FreeForm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402


def ffmpeg_like_id3(files, extra=()):
    """The ID3 tag ffmpeg makes of a Picard-tagged FLAC from another edition (9 tracks)."""
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        t = m.tags
        t.add(TRCK(encoding=3, text=["1"]))
        t.add(TPOS(encoding=3, text=["1"]))
        for desc, val in [("TOTALTRACKS", "9"), ("TRACKTOTAL", "9"), ("TOTALDISCS", "1"), ("DISCTOTAL", "1"),
                          *extra]:
            t.add(TXXX(encoding=3, desc=desc, text=[val]))
        m.save()


class CustomTagNamedLikeField(unittest.TestCase):
    def _run(self, files, targets=None):
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))

    def test_mp3_txxx_totaltracks(self):
        files = c.album(".mp3", 2)
        ffmpeg_like_id3(files)
        self._run(files)
        t = mutagen.File(str(files[1])).tags
        self.assertEqual(list(t["TRCK"].text), ["2/2"])
        self.assertEqual(list(t["TXXX:TOTALTRACKS"].text), ["9"], "Picard keeps the user tag as it is")

    def test_wav_txxx_totaltracks(self):
        files = c.album(".wav", 2)
        ffmpeg_like_id3(files)
        self._run(files)

    def test_mp3_txxx_title(self):
        files = c.album(".mp3", 2)
        ffmpeg_like_id3(files, [("TITLE", "old")])
        self._run(files)

    def test_mp3_txxx_albumartistsort(self):
        files = c.album(".mp3", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(TXXX(encoding=3, desc="ALBUMARTISTSORT", text=["Floyd, Pink"]))
            m.save()
        targets = c.proposal(files).targets
        for t in targets:
            t["albumartistsort"] = ["Pink Floyd"]
        self._run(files, targets)
        t = mutagen.File(str(files[0])).tags
        self.assertEqual(list(t["TSO2"].text), ["Pink Floyd"])
        self.assertNotIn("TXXX:ALBUMARTISTSORT", t, "Picard deletes it when it writes TSO2")

    def test_wavpack_ape_totaltracks(self):
        files = c.album(".wv", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags["Track"] = "1"
            m.tags["TOTALTRACKS"] = "9"
            m.save()
        self._run(files)
        t = mutagen.File(str(files[1])).tags
        self.assertEqual(str(t["Track"]), "2/2")
        self.assertEqual(str(t["TOTALTRACKS"]), "9", "Picard keeps the item as it is")

    def test_mp3_txxx_releasetype(self):
        # TXXX:RELEASETYPE (as Mp3tag and foobar2000 name it) beside Picard's own TXXX:MusicBrainz
        # Album Type: Picard keeps it as a custom tag and writes the release type in its frame
        files = c.album(".mp3", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(TXXX(encoding=3, desc="RELEASETYPE", text=["Album"]))
            m.save()
        targets = c.proposal(files).targets
        for t in targets:
            t["releasetype"] = ["album"]
        self._run(files, targets)

    def test_wavpack_ape_albumartist_key(self):
        # APEv2 "ALBUMARTIST" (Picard's key is "Album Artist"): Picard 3.0.2 reads it as the album
        # artist and writes the new value into it (checked end to end: ALBUMARTIST=Pink Floyd)
        files = c.album(".wv", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags["ALBUMARTIST"] = "Old Album Artist"
            m.save()
        self._run(files)

    def test_m4a_freeform_totaltracks(self):
        files = c.album(".m4a", 2)
        for f in files:
            m = mutagen.File(str(f))
            m.tags["----:com.apple.iTunes:TOTALTRACKS"] = [MP4FreeForm(b"9")]
            m.save()
        self._run(files)


if __name__ == "__main__":
    unittest.main()
