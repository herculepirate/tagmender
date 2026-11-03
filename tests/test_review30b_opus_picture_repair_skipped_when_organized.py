# Thirtieth independent review (B). Last round's fix is incomplete (the repair is new this round, so
# TM_REPO=prev fails too - it never repaired anything).
"""The Opus picture repair (a cover an earlier TagMender embedded with a width/height but no
colour depth, which libopusfile players refuse) is planned - fe["fix_pictures"] - but never
carried out when the profile moves or copies albums and the files already sit where the
naming puts them: exactly a library an earlier TagMender organized, run again.

  operation in place                    -> repaired
  operation move, to another folder     -> repaired
  operation move/copy, files already
  where the naming puts them            -> plan op "none", result "unchanged": still broken
                                           (the preview even lists "picture: repaired")

Cause: plan.py sets fe["op"] = "tag" for the repair (~line 976), but the naming step later
resets it from the tag changes only, ignoring fix_pictures (plan.py ~1143-1144):
    if Path(dst) == f or (...same_entry...):
        fe["dst"] = str(f)
        fe["op"] = "tag" if changes else "none"
(the copy path at ~1134, `op="tag" if changes2 else "none"`, likewise).

Fix: `"tag" if changes or fe.get("fix_pictures") else "none"` there (and on the copy's entry).
"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402

NAMING = "%albumartist%/%album%/$num(%tracknumber%,2) %title%"


def broken_cover(f):
    o = OggOpus(str(f))
    q = Picture()                     # as an earlier TagMender embedded it: a size, no depth
    q.type, q.mime, q.desc, q.data = 3, "image/jpeg", "", b"\xff\xd8\xff\xe0" + b"0" * 200
    q.width, q.height = 600, 600
    o["metadata_block_picture"] = [base64.b64encode(q.write()).decode()]
    o.save()


def still_broken(f):
    return any((q.width or q.height) and not q.depth
               for q in (Picture(base64.b64decode(b)) for b in OggOpus(str(f)).get("metadata_block_picture", [])))


class OpusRepairOrganized(unittest.TestCase):
    def _run(self, operation):
        c.fresh_state()
        lib = Path(tempfile.mkdtemp(prefix="tm30B-opuslib-"))
        album = lib / "Pink Floyd" / "The Dark Side of the Moon"
        files = [c.make(album / f"0{i} Track {i}.opus") for i in (1, 2)]
        targets = c.proposal(files).targets
        for f, t in zip(files, targets):          # tagged as the release has it already
            o = OggOpus(str(f))
            for k, v in t.items():
                o[k.upper()] = v
            o["TRACKTOTAL"], o["DISCTOTAL"], o["TOTALDISCS"] = t["totaltracks"], t["totaldiscs"], t["totaldiscs"]
            o.save()
            broken_cover(f)
        prof = c.profile(lib, (), None, operation=operation, destination=str(lib), naming={"template": NAMING})
        p = pl.build([c.proposal(files, targets)], prof)
        res = executor.Executor(p).run()
        self.assertTrue(all(fe.get("fix_pictures") for fe in p["albums"][0]["files"]), "repair planned")
        for f in files:
            self.assertFalse(still_broken(f), f"{operation}: {f.name} still has a cover with a size and no "
                                              f"depth (plan ops {[fe['op'] for fe in p['albums'][0]['files']]}, "
                                              f"result {res.get('counts')})")

    def test_move_profile_files_in_place(self):
        self._run("move")

    def test_copy_profile_files_in_place(self):
        self._run("copy")


if __name__ == "__main__":
    unittest.main()
