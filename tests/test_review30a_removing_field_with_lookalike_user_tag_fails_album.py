# Thirtieth independent review (A): last round's fix for "a user tag spelled like a field fails
# the album" (tagio.read's "~custom:NAME") covers a field being CHANGED, not one being REMOVED.
# Removing such a field still fails the whole album on every run.
#
# An ffmpeg-converted MP3 (TRCK "1/2" + TXXX:TOTALTRACKS "2"; or only TXXX:LABEL) and a field
# the job removes - a tagger script's $unset(totaltracks) / $unset(totaldiscs) (plan: a total
# a script unset in a combined pair is removed), or field policy "remove": the write takes the
# total out of TRCK (TPUB away), the TXXX stays (it is "a custom tag of its own"), and on the
# read-back the deferred custom tag is no longer "kept elsewhere" - tagio.read (tagio.py:567-
# 571) now reads it as the field itself. executor.file_step: "tag totaltracks did not read back
# as written; album put back as it was" - the album fails, on every later run too.
# Not a regression in effect (the previous version failed these albums too, earlier, on the
# changed sort tags), but the round's fix does not reach it.
# Fix: when a field is removed from an ID3/MP4/APEv2 file, also remove the user tag spelled as
# Picard spells the field (TXXX:TOTALTRACKS ...) - or read user tags spelled like a field kept
# elsewhere as "~custom:" always (then removal and the read-back agree).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPOS, TRCK, TXXX  # noqa: E402


def ffmpeg_like(lib, extra):
    files = []
    for n in (1, 2):
        f = c.encode(lib / "Band" / "Album" / f"0{n} Song.mp3", 400 + n * 40)
        t = ID3()
        t.add(TIT2(encoding=3, text=[f"old {n}"]))
        t.add(TPE1(encoding=3, text=["Band"]))
        t.add(TALB(encoding=3, text=["Album"]))
        t.add(TRCK(encoding=3, text=[f"{n}/2"]))
        t.add(TPOS(encoding=3, text=["1/1"]))
        for desc, val in extra.items():
            t.add(TXXX(encoding=3, desc=desc, text=[val]))
        t.save(str(f))
        files.append(f)
    return files


class RemoveFieldWithLookalike(unittest.TestCase):
    def _run(self, extra, **prof):
        tmp = c.fresh_state("tm30A-rm-")
        files = ffmpeg_like(tmp / "lib", extra)
        p = pl.build([c.proposal(files, c.targets(2))], c.profile(tmp / "lib", **prof))
        self.assertEqual(p["albums"][0]["status"], "ready", c.detail(p))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, c.detail(p, res))

    def test_script_unsets_total(self):
        self._run({"TOTALTRACKS": "2"},
                  picard={"enable_tagger_scripts": True, "list_of_scripts": ["$unset(totaltracks)"]})

    def test_script_unsets_disc_total(self):
        self._run({"TOTALDISCS": "1"},
                  picard={"enable_tagger_scripts": True, "list_of_scripts": ["$unset(totaldiscs)"]})

    def test_policy_removes_label(self):
        self._run({"LABEL": "Some Label"}, field_policy={"fields": {"label": "remove"}})


if __name__ == "__main__":
    unittest.main()
