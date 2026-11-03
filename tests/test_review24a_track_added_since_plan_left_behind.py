# Twenty-fourth independent review (A): the executor's "added since the plan" check - an album
# folder takes its cover, log, cue and lyrics along only while it holds just this album's music,
# "a track added since would stay behind alone" - now skips every companion named after one of
# the album's tracks ("01 a.lrc" beside "01 a.flac"), so that a shared folder's moving tracks can
# take their own lyrics. But it skips them in an album's own folder too: an album whose only
# companions are its tracks' lyrics is never checked. A track the user drops into the folder
# after the plan (a bonus track, a re-rip of a missing one) no longer stops the album: the job
# moves the album and its lyrics and leaves the new track alone in the old folder. The version
# before refused the album ("03 Track 3.flac was added to ... since the plan was made; plan again").
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review24a_common import Case, encode, executor  # noqa: E402


class AddedSincePlan(Case):
    def go(self, lyrics):
        album = self.lib / "Artist A" / "2001 - First"
        fs = self.album(album, "First", "Artist A")
        for f in fs:
            if lyrics:
                f.with_suffix(".lrc").write_text("[00:00.00]la la\n")
        if not lyrics:
            (album / "First.log").write_text("Exact Audio Copy extraction logfile\n")
        p = self.plan(self.profile("move"))
        self.assertTrue(p["albums"][0]["companions"])
        encode(album / "03 Track 3.flac", 999)                   # dropped in after the plan
        res = executor.Executor(p).run()
        a = res["albums"][0]
        self.assertEqual(a["status"], "conflict", f"the album was moved without the track added since: "
                         f"{sorted(x.name for x in album.iterdir())} stay in {album}")
        self.assertIn("since the plan was made", a["detail"])

    def test_album_with_a_rip_log(self):          # (passes: control)
        self.go(lyrics=False)

    def test_album_whose_companions_are_its_lyrics(self):
        self.go(lyrics=True)


if __name__ == "__main__":
    unittest.main()
