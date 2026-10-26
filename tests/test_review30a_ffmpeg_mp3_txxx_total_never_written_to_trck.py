# Thirtieth independent review (A): an MP3 converted by ffmpeg from a Picard-tagged FLAC
# keeps its track and disc totals only as user tags - TRCK "1", TPOS "1", TXXX:TOTALTRACKS "2",
# TXXX:TOTALDISCS "1" (ffmpeg's id3v2 muxer puts the number alone in TRCK/TPOS and every other
# Vorbis field in a TXXX) - and TagMender never writes the totals where players and Picard read
# them: after tagging, TRCK is still "1" and TPOS "1".
#
# tagio.read reads TXXX:TOTALTRACKS as the field totaltracks when TRCK holds no total (the
# split_custom rule makes it "~custom:TOTALTRACKS" only when the field is "kept elsewhere"),
# so the plan sees totaltracks 2 = the release's 2, plans no change, and _write_pair never
# rewrites TRCK. Picard reads TXXX:TOTALTRACKS as a custom tag of its own (_load_txxx_frame:
# "TOTALTRACKS" is none of its names) and writes TRCK "1/2", TPOS "1/1" (checked with
# picard/formats/id3.py). The preview shows nothing for the totals; a player shows track 1 of
# nothing; Picard, loading the file afterwards, sees totaltracks missing.
# The same root leaves the MusicBrainz IDs out: ffmpeg writes TXXX:MUSICBRAINZ_ALBUMID (the
# Vorbis name), which TagMender reads as musicbrainz_albumid - equal to the release's, so the
# frame Picard, Navidrome, beets and Jellyfin read (TXXX:MusicBrainz Album Id) is never written;
# likewise TXXX:RELEASETYPE (MusicBrainz Album Type), TXXX:LABEL (TPUB), TXXX:ARTISTSORT (TSOP),
# TXXX:ORIGINALDATE (TDOR) whenever they equal the release.
# Not a regression (the previous version merged the TXXX into the field always).
# Fix: read a TXXX (MP4 freeform, APEv2 item) spelled like one of Picard's fields that the
# format keeps elsewhere as "~custom:NAME" always - whether or not the field is there - so the
# field itself is seen as missing and written in its own frame.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX  # noqa: E402


class FfmpegMp3Totals(unittest.TestCase):
    def _run(self):
        tmp = c.fresh_state("tm30A-ffmp3-")
        lib = tmp / "lib"
        files = []
        for n in (1, 2):
            f = c.encode(lib / "Band" / "Album" / f"0{n} Song.mp3", 400 + n * 40)
            t = ID3()                                  # (as ffmpeg makes it from a Picard-tagged FLAC)
            t.add(TIT2(encoding=3, text=[f"old {n}"]))
            t.add(TPE1(encoding=3, text=["Band"]))
            t.add(TPE2(encoding=3, text=["Band"]))
            t.add(TALB(encoding=3, text=["Album"]))
            t.add(TRCK(encoding=3, text=[str(n)]))
            t.add(TXXX(encoding=3, desc="TOTALTRACKS", text=["2"]))
            t.add(TXXX(encoding=3, desc="TRACKTOTAL", text=["2"]))
            t.add(TPOS(encoding=3, text=["1"]))
            t.add(TXXX(encoding=3, desc="TOTALDISCS", text=["1"]))
            t.add(TXXX(encoding=3, desc="DISCTOTAL", text=["1"]))
            t.add(TXXX(encoding=3, desc="MUSICBRAINZ_ALBUMID", text=c.TARGET["musicbrainz_albumid"]))
            t.save(str(f))
            files.append(f)
        p = pl.build([c.proposal(files, c.targets(2))], c.profile(lib))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, c.detail(p, res))
        return files

    def test_totals_written_into_trck_and_tpos(self):
        files = self._run()
        for n, f in enumerate(files, 1):
            t = ID3(str(f))
            self.assertEqual(t["TIT2"].text, [f"Track {n}"], "(setup: the file was written)")
            self.assertEqual((str(t["TRCK"]), str(t["TPOS"])), (f"{n}/2", "1/1"),
                             "the totals stay in TXXX frames only: players (and Picard) see none")

    def test_musicbrainz_album_id_written_as_picard_names_it(self):
        for f in self._run():
            t = ID3(str(f))
            self.assertEqual([str(x) for x in t.getall("TXXX:MusicBrainz Album Id")], c.TARGET["musicbrainz_albumid"],
                             "the album ID is only in ffmpeg's TXXX:MUSICBRAINZ_ALBUMID, which players do not read")


if __name__ == "__main__":
    unittest.main()
