# Twenty-eighth independent review (A): a FLAC/Ogg track whose TRACKNUMBER holds its total
# ("5/9" - what ffmpeg and many converters write when they turn an MP3 into FLAC) gets a new
# total that never takes: TagMender writes TOTALTRACKS/TRACKTOTAL and leaves "5/9" as it was.
# Through a job (tagmender run/apply), the executor reads the tags back, finds the total is
# not what it wrote and fails the album: "01 Track 1.flac: tag totaltracks did not read back as
# written; album put back as it was" - every such album fails on every run, and is never tagged.
# tagio._write_pair (vorbis) now rewrites the number only when the number changes; the
# "inside" case (a total kept only in "5/9") is handled only when the number changes too. With
# the number the same and the total changed, TRACKNUMBER keeps its stale "/9". tagio.read (and
# Picard, and most players) take the total from "5/9" first and ignore TOTALTRACKS, so:
#  * written directly (tagio.write) the file says 9 tracks afterwards, not the 12 written;
#  * through a job the album fails and is put back - on every run (the watcher retries it).
# The previous version rewrote TRACKNUMBER as "5" with the totals in their own fields.
# Regression (passes with TM_REPO=prev).
# Fix: in _write_pair (vorbis), when the total changes and the number field holds "n/t",
# rewrite the number field as the number alone (as when the number changes) - i.e. treat
# `inside` as a reason to rewrite TRACKNUMBER/DISCNUMBER whenever tot is in changed.
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402
import tagio  # noqa: E402
import _review25a_common as c25  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402


class VorbisTotalInsideNumber(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm28A-vt-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_new_total_is_what_the_file_says_afterwards(self):
        p = c.encode(self.tmp / "05 Song.flac")
        f = FLAC(p)
        f["TRACKNUMBER"], f["DISCNUMBER"], f["TITLE"] = ["5/9"], ["1/1"], ["Song"]
        f.save()
        tagio.write(p, {"totaltracks": ["12"], "totaldiscs": ["2"]})   # what a job writes: the diff
        t = tagio.read(p)
        self.assertEqual((t.get("tracknumber"), t.get("totaltracks")), (["5"], ["12"]),
                         f"the file holds {dict(FLAC(p).tags.as_dict())}")
        self.assertEqual((t.get("discnumber"), t.get("totaldiscs")), (["1"], ["2"]))



class VorbisTotalInsideNumberJob(c25.Case):
    def test_album_with_n_of_t_numbers_is_tagged_and_settles(self):
        files = self.album(self.lib / "Band" / "2001 - Album", "Album", "Band", n=2)
        for k, f in enumerate(files, 1):                  # (as ffmpeg leaves an MP3 turned into FLAC)
            x = FLAC(f)
            x["TRACKNUMBER"], x["TITLE"] = [f"{k}/9"], [f"Album {k}"]
            x.save()
        prof = self.profile("in_place")
        res = c25.executor.Executor(self.plan(prof)).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0]["detail"])
        self.assertEqual(tagio.read(files[0]).get("totaltracks"), ["2"])
        again = [a for a in self.plan(prof)["albums"] if c25.pl.has_work(a)]
        self.assertEqual(again, [], "the next plan changes the totals again")


if __name__ == "__main__":
    unittest.main()
