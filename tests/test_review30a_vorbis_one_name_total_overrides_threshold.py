# Thirtieth independent review (A): last round's "a Vorbis total under one of its two names is
# written under both" (plan.py:1001-1007, v_one from tagio.vorbis_totals) bypasses diff_tags:
#
# 1. On a match below the replace threshold the preview says "kept (confidence below replace
#    threshold) totaltracks: 9 => 9", but the file's total is replaced by the release's: the
#    loop puts the TARGET value in changes when the field is not preserved (`... or not
#    target.get(tot_) else list(target[tot_])`), ignoring that diff_tags just refused it.
#    FLAC TRACKTOTAL=9 becomes TRACKTOTAL=2, TOTALTRACKS=2 on an 80% match.
# 2. The value is added to changes with no diff row: an album whose tags are all right already
#    (EAC/dBpoweramp/foobar2000 rips: TRACKTOTAL and DISCTOTAL only) shows "tag 01 ..." with
#    nothing listed under it and is rewritten (it was "unchanged" before). The same total is
#    silently added to every file an unresolved album is marked on (target is only TAGMENDER).
# Regression: all pass with TM_REPO=prev.
# Fix: only when the file is written for another reason (changes not empty after diff_tags),
# and only with the value diff_tags settled on - the file's own when it kept it (below
# threshold, preserve, lock, fill_missing, level) - and add a row ("added TOTALTRACKS beside
# TRACKTOTAL") so the preview says why the file is written.
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402


def rip(lib, total, tg):
    files = []
    for i, t in enumerate(tg):
        f = c.encode(lib / "Band" / "Album" / f"0{i + 1} t.flac", 400 + i * 30)
        m = mutagen.File(f)
        for k, v in t.items():
            if k == "totaltracks":
                m["TRACKTOTAL"] = [total]            # (EAC, dBpoweramp, foobar2000)
            elif k == "totaldiscs":
                m["DISCTOTAL"] = v
            else:
                m[k.upper()] = v
        m.save()
        files.append(f)
    return files


class VorbisOneNameTotal(unittest.TestCase):
    def test_below_replace_threshold_total_is_kept(self):
        tmp = c.fresh_state("tm30A-tt-")
        tg = c.targets(2)
        files = rip(tmp / "lib", "9", tg)
        p = pl.build([c.proposal(files, tg, confidence=80.0)], c.profile(tmp / "lib"))
        rows = [r for fe in p["albums"][0]["files"] for r in fe["diff"] if r["field"] == "totaltracks"]
        self.assertTrue(rows and all(r["action"].startswith("kept") for r in rows), "(setup: the preview says kept)")
        executor.Executor(p).run()
        for f in files:
            m = mutagen.File(f)
            self.assertEqual((m.get("TRACKTOTAL"), m.get("TOTALTRACKS", ["9"])), (["9"], ["9"]),
                             "the preview said the total is kept (below the replace threshold); the file "
                             "got the release's total")

    def test_file_written_only_when_the_preview_says_why(self):
        tmp = c.fresh_state("tm30A-tt-")
        tg = c.targets(2)
        files = rip(tmp / "lib", "2", tg)          # every tag as the release has it already
        p = pl.build([c.proposal(files, tg)], c.profile(tmp / "lib"))
        for fe in p["albums"][0]["files"]:
            if fe["op"] != "none":
                self.assertTrue(fe["diff"], f"{Path(fe['src']).name} is written ({fe['changes']}) but the "
                                            "preview lists no change for it")

    def test_unresolved_mark_writes_only_the_mark(self):
        tmp = c.fresh_state("tm30A-tt-")
        tg = c.targets(2)
        files = rip(tmp / "lib", "2", tg)
        prop = pl.Proposal(source_dir=str(files[0].parent), files=[str(f) for f in files], status="unresolved",
                           reason="nothing found", reason_codes=["no_match"])
        p = pl.build([prop], c.profile(tmp / "lib", unresolved={"action": "mark"}))
        for fe in p["albums"][0]["files"]:
            shown = {r["field"] for r in fe["diff"]}
            self.assertEqual(set(fe["changes"]) - shown, set(),
                             "marking an unresolved album writes fields the preview does not show")


if __name__ == "__main__":
    unittest.main()
