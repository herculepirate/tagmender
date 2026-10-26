# Thirtieth independent review (A): last round's repair of the Opus covers the previous
# release wrote (a size and no colour depth - libopusfile players show no cover) is never done
# in a library a move or copy profile has already organized, though the preview promises it.
#
# plan.py:990-996 sets fe["fix_pictures"], fe["op"] = "tag" and adds the row "repaired
# picture: size without depth => no size (Opus players show it)". For a move/copy profile the
# destination is worked out afterwards; when the file already is where the naming puts it
# (plan.py:1142-1144: `fe["op"] = "tag" if changes else "none"`) - every album the previous
# release moved or copied into the library, planned again - the op is reset to "none". The
# album is listed UNCHANGED with the "repaired" row under each file, nothing is written, and
# the covers stay broken on every later run. (The same reset happens for a hard-linked
# destination of a copy - plan.py:1149-1150 - and _relink_step ignores fix_pictures.) A copy
# profile run again over its sources finds the previous release's copies as earlier copies
# (plan.py:1108-1137), whose op comes from the copy's tag changes only and whose own pictures
# are never looked at: the copies keep their broken covers.
# Not a regression (the previous version repaired nothing), but the preview now says otherwise.
# Fix: keep op "tag" when fe["fix_pictures"] (`"tag" if changes or fe.get("fix_pictures")`),
# in the linked-copy branch too, pass fix_pictures through _relink_step, and in the earlier-copy
# branch set fix_pictures from opus_pictures_need_fix(dst) (with its row).
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from mutagen.flac import Picture  # noqa: E402
from mutagen.oggopus import OggOpus  # noqa: E402


def previous_release_cover(f, data):
    o = OggOpus(str(f))
    q = Picture()
    q.type, q.mime, q.desc, q.data = 3, "image/jpeg", "", data
    q.width = q.height = 300                     # (as the previous release embedded it: no depth)
    o["metadata_block_picture"] = [base64.b64encode(q.write()).decode()]
    o.save()


class OpusRepairInOrganizedLibrary(unittest.TestCase):
    def _organized(self, op):
        tmp = c.fresh_state("tm30A-opus-")
        lib, dest = tmp / "lib", tmp / "library"
        dest.mkdir()
        data = c.jpeg_bytes((100, 20, 20))
        tg = c.targets(2)
        files = [c.encode(lib / "Band" / "Album" / f"0{i + 1} t.opus", 400 + i * 30) for i in range(2)]
        for f, t in zip(files, tg):
            tagio.write(f, t)
        prof = c.profile(lib, operation=op, destination=str(dest))
        p = pl.build([c.proposal(files, tg)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, c.detail(p, res))
        placed = [Path(fe["dst"]) for fe in p["albums"][0]["files"]]
        for f in placed:                              # (the library as the previous release left it)
            previous_release_cover(f, data)
            self.assertTrue(tagio.opus_pictures_need_fix(f))
        # the profile run again over its library: the albums are where the naming puts them
        again = c.profile(dest, operation=op, destination=str(dest))
        p2 = pl.build([c.proposal(placed, tg)], again)
        promised = [fe for fe in p2["albums"][0]["files"] if any(r["action"] == "repaired" for r in fe["diff"])]
        self.assertEqual(len(promised), 2, "(setup: the preview promises the repair)")
        executor.Executor(p2).run()
        for f in placed:
            self.assertFalse(tagio.opus_pictures_need_fix(f),
                             f"{f.name}: the preview said 'repaired' (op {promised[0]['op']!r}); the cover "
                             "still has a size and no depth")

    def test_move_profile(self):
        self._organized("move")

    def test_copy_profile(self):
        self._organized("copy")

    def test_copy_profile_run_again_over_its_sources(self):
        # a copy profile run again over its (never written) sources: the copies the previous
        # release made are found as earlier copies (plan.py:1108-1137) and brought up to date -
        # the earlier-copy branch sets op from the copy's tag changes only, so a broken cover in
        # the copy is never repaired (and the preview says nothing about it)
        tmp = c.fresh_state("tm30A-opus-")
        lib, dest = tmp / "lib", tmp / "library"
        dest.mkdir()
        data = c.jpeg_bytes((100, 20, 20))
        tg = c.targets(2)
        files = [c.encode(lib / "Band" / "Album" / f"0{i + 1} t.opus", 400 + i * 30) for i in range(2)]
        for f, t in zip(files, tg):
            tagio.write(f, t)
            previous_release_cover(f, data)
        prof = c.profile(lib, operation="copy", destination=str(dest))
        p = pl.build([c.proposal(files, tg)], prof)
        executor.Executor(p).run()
        copies = [Path(fe["dst"]) for fe in p["albums"][0]["files"]]
        for f in copies:                              # (the copies as the previous release made them)
            previous_release_cover(f, data)
        p2 = pl.build([c.proposal(files, tg)], prof)
        self.assertTrue(all(fe.get("copy_of") for fe in p2["albums"][0]["files"]), "(setup: earlier copies found)")
        executor.Executor(p2).run()
        for f in copies:
            self.assertFalse(tagio.opus_pictures_need_fix(f), f"{f.name}: the copy's cover is never repaired")

    def _linked(self, op):
        # the destination already is a hard link to the track (plan: fe["linked"])
        import os
        tmp = c.fresh_state("tm30A-opus-")
        lib, dest = tmp / "lib", tmp / "library"
        dest.mkdir()
        tg = c.targets(2)
        files = [c.encode(lib / "Band" / "Album" / f"0{i + 1} t.opus", 400 + i * 30) for i in range(2)]
        for f, t in zip(files, tg):
            tagio.write(f, t)
            previous_release_cover(f, c.jpeg_bytes((1, 2, 3)))
        prof = c.profile(lib, operation=op, destination=str(dest))
        for fe in pl.build([c.proposal(files, tg)], prof)["albums"][0]["files"]:
            Path(fe["dst"]).parent.mkdir(parents=True, exist_ok=True)
            os.link(fe["src"], fe["dst"])
        p = pl.build([c.proposal(files, tg)], prof)
        fes = p["albums"][0]["files"]
        self.assertTrue(all(fe.get("linked") and any(r["action"] == "repaired" for r in fe["diff"]) for fe in fes),
                        "(setup: linked, repair promised)")
        executor.Executor(p).run()
        for fe in fes:
            self.assertFalse(tagio.opus_pictures_need_fix(fe["dst"]),
                             f"{Path(fe['dst']).name} (op {fe['op']!r}, a hard link): the preview said 'repaired'; "
                             "the cover still has a size and no depth")

    def test_move_onto_hard_link(self):
        self._linked("move")

    def test_copy_onto_hard_link(self):
        self._linked("copy")


if __name__ == "__main__":
    unittest.main()
