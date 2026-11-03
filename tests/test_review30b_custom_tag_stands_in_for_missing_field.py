# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too: the merge
# predates this round; last round's fix split the tag off only when the field is also present).
"""A user tag spelled like one of the fields - TXXX:TOTALTRACKS / TXXX:TOTALDISCS (ffmpeg's
conversion of a Picard-tagged FLAC to MP3 writes exactly these, with TRCK "1" and TPOS "1"),
TXXX:ALBUMARTISTSORT (older taggers), TXXX:RELEASETYPE / RELEASESTATUS / RELEASECOUNTRY
(Mp3tag's and foobar2000's names), an M4A ----:com.apple.iTunes:TOTALTRACKS beside trkn
(1, 0), an APEv2 TOTALTRACKS item beside Track "1" - stands in for the real field when the
file has nothing in the field's own place. When its value equals the release's, TagMender
plans no change and never writes the field where Picard (and players) read it:

  MP3 TRCK "1", TPOS "1", TXXX:TOTALTRACKS "2", TXXX:TOTALDISCS "1"
      Picard: TRCK "1/2", TPOS "1/1"          TagMender: TRCK "1", TPOS "1"   (no totals)
  MP3 converted by ffmpeg from a Picard-tagged FLAC: TXXX:MUSICBRAINZ_ALBUMID, TXXX:ORIGINALDATE,
      TXXX:MEDIA (Vorbis names) equal to the release's
      Picard: TXXX:MusicBrainz Album Id, TDOR, TMED written
      TagMender: none of them - the converted MP3 never gets the album ID frame Picard and
      players read (real ffmpeg output checked end to end: r30B_work/j_ffconv.py)
  MP3 TXXX:ALBUMARTISTSORT "Pink Floyd"
      Picard: TSO2 "Pink Floyd" (TXXX deleted)   TagMender: no TSO2
  MP3 TXXX:RELEASETYPE/RELEASESTATUS/RELEASECOUNTRY = the release's
      Picard: TXXX:MusicBrainz Album Type / Status / Release Country written
      TagMender: none of them (Picard, reading TagMender's file, sees no release type ...)
  M4A trkn (1, 0) + freeform TOTALTRACKS "2"  ->  Picard trkn (1, 2); TagMender (1, 0)
  WavPack Track "1" + TOTALTRACKS "2"         ->  Picard Track "1/2"; TagMender "1"

Checked end to end with Picard's own code (r30B_work/j_cust2.py); also seen in the foreign-tag
second-run sweep ("PICARD-SEES releasetype: [] -> ['album']" on TagMender's output).

Cause: tagio.read (~lines 567-571, this round):
    for name, orig, vals in deferred or ():
        if name in out: _add(out, "~custom:" + orig, vals)
        else:           _add(out, name, vals)          # <- the user tag becomes the field
so `before` has totaltracks=2 / albumartistsort / releasetype from the user tag, diff_tags sees
the release's value already there, and the field's own frame/atom/item is never written.
Picard never reads an ID3 TXXX or MP4 freeform user tag as the field (its names are
case-sensitive); whatever it reads, it always writes every field in its own place on save
(APEv2: it does read TOTALTRACKS as totaltracks, and writes Track "1/2").

Fix: ID3/MP4: always read such a user tag as a tag of its own (never as the field), so the field
is planned as missing and written in its own place; APEv2: plan the pair/field as a change when
its own item (Track, Disc ...) does not hold it.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TPOS, TRCK, TXXX
from mutagen.mp4 import MP4FreeForm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def tag(files, fn):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        fn(m.tags)
        m.save()


class UserTagStandsInForField(unittest.TestCase):
    def test_mp3_ffmpeg_totals(self):
        files = c.album(".mp3", 2)
        tag(files, lambda t: [t.add(TRCK(encoding=3, text=["1"])), t.add(TPOS(encoding=3, text=["1"])),
                              t.add(TXXX(encoding=3, desc="TOTALTRACKS", text=["2"])),
                              t.add(TXXX(encoding=3, desc="TOTALDISCS", text=["1"]))])
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertEqual((str(t["TRCK"]), str(t["TPOS"])), ("1/2", "1/1"), "Picard writes the totals into TRCK/TPOS")

    def test_mp3_albumartistsort(self):
        files = c.album(".mp3", 2)
        tag(files, lambda t: t.add(TXXX(encoding=3, desc="ALBUMARTISTSORT", text=["Pink Floyd"])))
        targets = c.proposal(files).targets
        for t in targets:
            t["albumartistsort"] = ["Pink Floyd"]
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertIn("TSO2", t, "Picard writes TSO2 (and deletes TXXX:ALBUMARTISTSORT)")

    def test_mp3_mp3tag_release_type(self):
        files = c.album(".mp3", 2)
        tag(files, lambda t: [t.add(TXXX(encoding=3, desc="RELEASETYPE", text=["album"])),
                              t.add(TXXX(encoding=3, desc="RELEASESTATUS", text=["official"]))])
        targets = c.proposal(files).targets
        for t in targets:
            t.update(releasetype=["album"], releasestatus=["official"])
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertEqual(sorted(k for k in t if k in ("TXXX:MusicBrainz Album Type", "TXXX:MusicBrainz Album Status")),
                         ["TXXX:MusicBrainz Album Status", "TXXX:MusicBrainz Album Type"])

    def test_mp3_ffmpeg_musicbrainz_ids_and_dates(self):
        # what ffmpeg writes converting a Picard-tagged FLAC (Vorbis names in TXXX frames)
        files = c.album(".mp3", 2)
        tag(files, lambda t: [t.add(TXXX(encoding=3, desc="MUSICBRAINZ_ALBUMID", text=[c.TARGET["musicbrainz_albumid"][0]])),
                              t.add(TXXX(encoding=3, desc="ORIGINALDATE", text=["1973-03-01"])),
                              t.add(TXXX(encoding=3, desc="MEDIA", text=["CD"]))])
        targets = c.proposal(files).targets
        for t in targets:
            t.update(originaldate=["1973-03-01"], media=["CD"])
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertEqual(sorted(k for k in ("TXXX:MusicBrainz Album Id", "TDOR", "TMED") if k in t),
                         ["TDOR", "TMED", "TXXX:MusicBrainz Album Id"],
                         "Picard writes the album ID, original date and media in their own frames")

    def test_m4a_trkn_total(self):
        files = c.album(".m4a", 2)
        tag(files, lambda t: [t.__setitem__("trkn", [(1, 0)]),
                              t.__setitem__("----:com.apple.iTunes:TOTALTRACKS", [MP4FreeForm(b"2")])])
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(mutagen.File(str(files[0])).tags["trkn"], [(1, 2)])

    def test_wavpack_track_total(self):
        files = c.album(".wv", 2)
        tag(files, lambda t: [t.__setitem__("Track", "1"), t.__setitem__("TOTALTRACKS", "2")])
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(str(mutagen.File(str(files[0])).tags["Track"]), "1/2")


if __name__ == "__main__":
    unittest.main()
